"""海拔剖面与累计爬升分析（基于上传后保存的原始 GPX 轨迹，不经过遮蔽裁切）。

- 对每个原始 trkseg 的连续有效海拔区间，按水平里程每 20 米线性重采样，
  区间端点也保留；缺失海拔处断开，不跨原始分段或缺口插值；
- 对采样海拔做三点中位数平滑（端点不变），抑制孤立尖峰；
- 再以 5 米反向变化确认峰谷，累计已确认的上升与下降，
  尾端未确认的变化也计入；
- 全程无任何有效海拔时 has_elevation 为 False，爬升/下降为 None，
  由页面显示"无海拔数据"而非零爬升。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .geo import haversine_m

SAMPLE_STEP_M = 20.0        # 重采样水平步长（米）
REVERSAL_THRESHOLD_M = 5.0  # 峰谷确认所需的反向变化（米）

_EPS_M = 1e-6  # 里程比较的浮点噪声阈值（米）


@dataclass
class ProfileInterval:
    """一段连续有效海拔区间的重采样剖面；里程为全程累计水平里程。"""

    seg_index: int          # 原始 trkseg 序号（从 1 开始）
    points: list            # list[(distance_m, ele)]，重采样 + 中位数平滑后
    ascent_m: float
    descent_m: float


@dataclass
class ElevationProfile:
    has_elevation: bool
    intervals: list         # list[ProfileInterval]
    distance_m: float       # 全程水平里程（含无海拔区间，各 trkseg 连续累计）
    ascent_m: Optional[float]
    descent_m: Optional[float]
    ele_min: Optional[float]
    ele_max: Optional[float]


def _resample_interval(cum, eles, i0, i1, step_m):
    """把 [i0, i1] 区间按水平里程每 step_m 米线性重采样，区间端点也保留。

    cum: 整个 trkseg 的累计水平里程（与轨迹点一一对应）；
    eles: 该区间各轨迹点的海拔（eles[k] 对应轨迹点 i0+k，均有效）。
    返回 list[(distance_m, ele)]，distance 为 trkseg 内里程。
    """
    start_d = cum[i0]
    end_d = cum[i1]
    if end_d - start_d <= _EPS_M:  # 单点或原地不动的区间
        return [(start_d, eles[0])]

    grid = []
    d = start_d
    while d < end_d - _EPS_M:
        grid.append(d)
        d += step_m
    grid.append(end_d)  # 区间端点也保留

    out = []
    j = 0  # 区间内的采样线段指针，随 grid 单调推进
    for d in grid:
        while i0 + j < i1 and cum[i0 + j + 1] < d:
            j += 1
        k = i0 + j
        if k >= i1:
            out.append((d, eles[-1]))
            continue
        seg_len = cum[k + 1] - cum[k]
        if seg_len <= 0.0:  # 相邻点重合的零长度线段
            out.append((d, eles[j + 1]))
        else:
            t = min(max((d - cum[k]) / seg_len, 0.0), 1.0)
            out.append((d, eles[j] + (eles[j + 1] - eles[j]) * t))
    return out


def _median3(values):
    """三点中位数滤波，抑制孤立尖峰；序列端点保持不变。"""
    n = len(values)
    if n < 3:
        return list(values)
    out = [values[0]]
    for i in range(1, n - 1):
        out.append(sorted((values[i - 1], values[i], values[i + 1]))[1])
    out.append(values[-1])
    return out


def _climb_totals(eles, threshold_m):
    """以 threshold_m 米反向变化确认峰谷，返回 (总爬升, 总下降)。

    反向变化达到阈值时确认一个峰/谷，累计自上一确认点以来的上升/下降；
    不足阈值的反向抖动视为噪声忽略；尾端未确认的变化也计入。
    """
    ascent = 0.0
    descent = 0.0
    if len(eles) < 2:
        return ascent, descent
    anchor = eles[0]    # 最近一个已确认峰/谷（或起点）的海拔
    extreme = eles[0]   # 当前方向上的候选极值
    trend = 0           # +1 上升中，-1 下降中，0 尚未定向
    for y in eles[1:]:
        if trend == 0:
            if y > extreme:
                extreme = y
                trend = 1
            elif y < extreme:
                extreme = y
                trend = -1
        elif trend > 0:
            if y >= extreme:
                extreme = y
            elif extreme - y >= threshold_m:  # 确认一个峰
                ascent += extreme - anchor
                anchor = extreme
                extreme = y
                trend = -1
        else:
            if y <= extreme:
                extreme = y
            elif y - extreme >= threshold_m:  # 确认一个谷
                descent += anchor - extreme
                anchor = extreme
                extreme = y
                trend = 1
    # 尾端未确认的变化也计入
    if trend > 0:
        ascent += extreme - anchor
    elif trend < 0:
        descent += anchor - extreme
    return ascent, descent


def elevation_profile(segments, step_m=SAMPLE_STEP_M, threshold_m=REVERSAL_THRESHOLD_M):
    """对原始轨迹（list[list[TrackPoint]]）计算海拔剖面与累计爬升/下降。

    里程横轴沿各 trkseg 连续累计（仅用于定位，不跨分段或缺口插值）。
    """
    intervals = []
    distance_m = 0.0
    ascent = 0.0
    descent = 0.0
    ele_min = None
    ele_max = None

    for seg_idx, seg in enumerate(segments, start=1):
        if not seg:
            continue
        cum = [0.0]
        for a, b in zip(seg, seg[1:]):
            cum.append(cum[-1] + haversine_m(a, b))
        seg_base = distance_m
        distance_m += cum[-1]

        n = len(seg)
        i = 0
        while i < n:
            if seg[i].ele is None:
                i += 1
                continue
            j = i
            while j + 1 < n and seg[j + 1].ele is not None:
                j += 1
            # [i, j] 为一个连续有效海拔区间
            eles = [p.ele for p in seg[i:j + 1]]
            samples = _resample_interval(cum, eles, i, j, step_m)
            smoothed = _median3([e for _, e in samples])
            up, down = _climb_totals(smoothed, threshold_m)
            points = [(seg_base + d, e) for (d, _), e in zip(samples, smoothed)]
            intervals.append(ProfileInterval(
                seg_index=seg_idx, points=points, ascent_m=up, descent_m=down,
            ))
            ascent += up
            descent += down
            for _, e in points:
                ele_min = e if ele_min is None else min(ele_min, e)
                ele_max = e if ele_max is None else max(ele_max, e)
            i = j + 1

    has_elevation = bool(intervals)
    return ElevationProfile(
        has_elevation=has_elevation,
        intervals=intervals,
        distance_m=distance_m,
        ascent_m=ascent if has_elevation else None,
        descent_m=descent if has_elevation else None,
        ele_min=ele_min,
        ele_max=ele_max,
    )
