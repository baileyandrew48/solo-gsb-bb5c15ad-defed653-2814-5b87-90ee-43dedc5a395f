"""海拔剖面与累计爬升分析的单元测试。

可直接运行：python3 tests/test_elevation.py
或使用 pytest：python3 -m pytest tests/
"""
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.elevation import _climb_totals, _median3, elevation_profile
from app.geo import EARTH_RADIUS_M, TrackPoint

T0 = datetime(2026, 5, 1, 8, 0, 0, tzinfo=timezone.utc)

# 纯纬度变化时球面距离 = R * Δlat(rad)，反解每步给定米数对应的纬度差
LAT_PER_100M = math.degrees(100.0 / EARTH_RADIUS_M)
LAT_PER_50M = math.degrees(50.0 / EARTH_RADIUS_M)
LAT_PER_20M = math.degrees(20.0 / EARTH_RADIUS_M)


def make_seg(points):
    """points: [(lat, lon, ele 或 None, 秒偏移), ...]"""
    return [
        TrackPoint(lat=la, lon=lo, ele=el, time=T0 + timedelta(seconds=s))
        for la, lo, el, s in points
    ]


def north_seg(eles, lat_per_step=LAT_PER_100M, start_lat=31.0):
    """沿正北等距（默认每步 100 m）的轨迹段，海拔取给定序列（可含 None）。"""
    return make_seg([
        (start_lat + i * lat_per_step, 121.0, el, i * 10)
        for i, el in enumerate(eles)
    ])


def test_resample_grid_and_endpoints():
    """每 20 米重采样且区间端点保留；采样值按水平里程线性插值。"""
    # 4 个点，累计里程 0 / 100 / 200 / 250 m，海拔单调（三点中位数不改变单调序列）
    seg = make_seg([
        (31.0, 121.0, 100.0, 0),
        (31.0 + LAT_PER_100M, 121.0, 110.0, 10),
        (31.0 + 2 * LAT_PER_100M, 121.0, 130.0, 20),
        (31.0 + 2 * LAT_PER_100M + LAT_PER_50M, 121.0, 135.0, 30),
    ])
    profile = elevation_profile([seg])
    assert profile.has_elevation
    assert len(profile.intervals) == 1
    pts = profile.intervals[0].points

    # 网格 0,20,...,240 共 13 点 + 区间端点 250 → 14 点
    assert len(pts) == 14
    assert abs(pts[0][0] - 0.0) < 1e-6 and abs(pts[0][1] - 100.0) < 1e-6
    assert abs(pts[5][0] - 100.0) < 1e-4 and abs(pts[5][1] - 110.0) < 1e-6
    assert abs(pts[10][0] - 200.0) < 1e-4 and abs(pts[10][1] - 130.0) < 1e-6
    # 240 m 处：在 200 m(130) 与 250 m(135) 之间线性插值 → 134
    assert abs(pts[12][0] - 240.0) < 1e-4 and abs(pts[12][1] - 134.0) < 1e-6
    # 端点 250 m 保留（不是 20 的倍数也列出）
    assert abs(pts[-1][0] - 250.0) < 1e-4 and abs(pts[-1][1] - 135.0) < 1e-6
    # 单调上升：爬升为末-初，无下降
    assert abs(profile.ascent_m - 35.0) < 1e-6
    assert abs(profile.descent_m - 0.0) < 1e-9


def test_missing_elevation_breaks_intervals():
    """缺失海拔处断开：不跨缺口插值，各区间独立分析。"""
    seg = north_seg([100.0, None, 120.0, 130.0, None, 200.0])
    profile = elevation_profile([seg])

    assert profile.has_elevation
    assert len(profile.intervals) == 3  # [点0]、[点2-3]、[点5]

    # 单点区间：只有一个采样，不产生爬升
    assert len(profile.intervals[0].points) == 1
    assert abs(profile.intervals[0].points[0][0] - 0.0) < 1e-6
    assert profile.intervals[0].ascent_m == 0.0

    # 中间区间从缺口后的第一个有效点开始（里程 200 m），不向缺口内插值
    mid = profile.intervals[1]
    assert abs(mid.points[0][0] - 200.0) < 1e-4
    assert abs(mid.points[0][1] - 120.0) < 1e-6
    assert abs(mid.points[-1][0] - 300.0) < 1e-4
    assert abs(mid.ascent_m - 10.0) < 1e-6

    # 缺口距离仍计入全程里程横轴
    assert abs(profile.distance_m - 500.0) < 1e-4
    assert abs(profile.ascent_m - 10.0) < 1e-6
    assert abs(profile.ele_min - 100.0) < 1e-6
    assert abs(profile.ele_max - 200.0) < 1e-6


def test_trkseg_boundaries_not_interpolated():
    """不跨原始 trkseg 插值：每个分段独立成区间，里程横轴连续累计。"""
    seg1 = north_seg([100.0, 150.0])
    seg2 = north_seg([200.0, 250.0], start_lat=31.01)
    profile = elevation_profile([seg1, seg2])

    assert len(profile.intervals) == 2
    assert profile.intervals[0].seg_index == 1
    assert profile.intervals[1].seg_index == 2
    # 第二个 trkseg 的剖面横轴接在第一段里程（约 100 m）之后
    assert abs(profile.intervals[1].points[0][0] - 100.0) < 1e-4
    assert abs(profile.distance_m - 200.0) < 1e-4
    # 两段各爬升 50 m；跨段的高差（150→200）不计入
    assert abs(profile.ascent_m - 100.0) < 1e-6
    assert abs(profile.descent_m - 0.0) < 1e-9


def test_median3_suppresses_isolated_spike():
    """三点中位数抑制孤立尖峰：单个采样点的 +30 m 尖峰不计入爬升。"""
    assert _median3([100.0, 130.0, 100.0]) == [100.0, 100.0, 100.0]
    # 端点保持不变
    assert _median3([5.0, 1.0, 1.0, 1.0, 9.0]) == [5.0, 1.0, 1.0, 1.0, 9.0]
    assert _median3([3.0, 8.0]) == [3.0, 8.0]

    # 原始点距 20 m，与重采样网格对齐，尖峰只占一个采样点
    seg = north_seg(
        [100.0, 100.0, 100.0, 130.0, 100.0, 100.0, 100.0],
        lat_per_step=LAT_PER_20M,
    )
    profile = elevation_profile([seg])
    assert profile.ascent_m < 0.01
    assert profile.descent_m < 0.01


def test_reversal_threshold_confirms_peak_valley():
    """5 米反向变化确认峰谷；不足 5 米的反向抖动忽略；恰好 5 米确认。"""
    # 起伏 10 m：爬升 20、下降 20（尾端下降也计入）
    up, down = _climb_totals([0.0, 10.0, 0.0, 10.0, 0.0], 5.0)
    assert abs(up - 20.0) < 1e-9 and abs(down - 20.0) < 1e-9
    # 中途 4 m 回落不足阈值，被忽略
    up, down = _climb_totals([0.0, 3.0, 6.0, 2.0, 8.0], 5.0)
    assert abs(up - 8.0) < 1e-9 and abs(down - 0.0) < 1e-9
    # 恰好 5 米反向变化即确认
    up, down = _climb_totals([0.0, 5.0, 0.0], 5.0)
    assert abs(up - 5.0) < 1e-9 and abs(down - 5.0) < 1e-9
    # 先降后升：谷被确认
    up, down = _climb_totals([100.0, 90.0, 95.0], 5.0)
    assert abs(up - 5.0) < 1e-9 and abs(down - 10.0) < 1e-9


def test_tail_unconfirmed_change_counted():
    """尾端未确认的变化也计入；平缓和零变化不产生爬升。"""
    up, down = _climb_totals([0.0, 4.0, 0.0], 5.0)  # 尾端上升 4 m 未确认仍计入
    assert abs(up - 4.0) < 1e-9 and abs(down - 0.0) < 1e-9
    up, down = _climb_totals([100.0, 103.0], 5.0)
    assert abs(up - 3.0) < 1e-9 and abs(down - 0.0) < 1e-9
    assert _climb_totals([7.0, 7.0, 7.0], 5.0) == (0.0, 0.0)
    assert _climb_totals([5.0], 5.0) == (0.0, 0.0)
    assert _climb_totals([], 5.0) == (0.0, 0.0)

    # 端到端：单调上升 3 m 的轨迹，总爬升 3 m
    profile = elevation_profile([north_seg([100.0, 103.0])])
    assert abs(profile.ascent_m - 3.0) < 1e-6


def test_no_elevation_at_all():
    """全无有效海拔：has_elevation 为 False，爬升/下降为 None（页面显示无海拔数据）。"""
    profile = elevation_profile([north_seg([None, None, None])])
    assert not profile.has_elevation
    assert profile.intervals == []
    assert profile.ascent_m is None
    assert profile.descent_m is None
    assert profile.ele_min is None and profile.ele_max is None
    # 水平里程仍正常累计
    assert abs(profile.distance_m - 200.0) < 1e-4


def test_single_point_and_stationary_intervals():
    """单点区间与原地不动的区间：各列一个采样，不产生爬升。"""
    single = elevation_profile([north_seg([100.0])])
    assert single.has_elevation
    assert len(single.intervals) == 1
    assert len(single.intervals[0].points) == 1
    assert single.ascent_m == 0.0

    stationary = make_seg([
        (31.0, 121.0, 100.0, 0),
        (31.0, 121.0, 110.0, 10),  # 位置不变，海拔不同也不插值出距离
    ])
    profile = elevation_profile([stationary])
    assert len(profile.intervals) == 1
    assert len(profile.intervals[0].points) == 1
    assert profile.ascent_m == 0.0


def _run_all():
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
