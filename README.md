# 徒步轨迹分享 · 敏感地点遮蔽

上传含时间的 GPX 轨迹，自动统计**分段配速**；在**无底图**预览图上点击敏感地点（如家、公司）
并设定遮蔽半径，系统按**轨迹线段与遮蔽圆的交点**精确裁切，导出为**互不连接的 GPX `trkseg`** 后再分享。

- 后端：Python / FastAPI（几何裁切与配速统计在服务端完成）
- 前端：原生 JavaScript + Canvas（无任何框架、无地图底图依赖）

## 一条命令启动

```bash
docker compose up --build
```

依赖安装与初始化全部在镜像构建和容器启动时自动完成，无需任何额外步骤。

## 访问地址

- 网页：**http://localhost:8000**（端口 8000）

## 配置

| 配置项 | 位置 | 说明 |
| --- | --- | --- |
| 对外端口 | `docker-compose.yml` 中 `ports` | 默认 `"8000:8000"`，改左侧即可（如 `"8080:8000"`） |
| 上传大小限制 | `app/main.py` 中 `MAX_UPLOAD_BYTES` | 默认 50 MB |
| 每段里程 | 页面"分段配速"输入框，或 `/api/pace` 的 `split_km` | 0.1～10 公里，默认 1 公里 |
| 数据存储 | 内存 | 演示用途：轨迹仅存内存，容器重启即清空，无需数据库 |

无其他必需配置（不需要 API Key、不依赖外部服务）。

## 使用流程

1. **上传 GPX**：选择含 `<time>` 的 GPX 文件（缺少时间会被拒绝并提示）。可用
   `examples/sample.gpx` 快速体验。
2. **查看分段配速**：上传后页面下方自动按**每段里程**（0.1～10 公里，默认 1 公里，
   可修改后自动重算）列出每段的距离、耗时、分/公里配速与起止时间。
3. **设置遮蔽**：在右侧预览图上**点击**敏感地点即添加遮蔽圆，半径在左侧面板设定
   （添加前设定新圆半径；已有圆可在列表中单独修改或删除）。
4. **查看裁切结果**：页面实时显示 **隐藏段数**、**裁切前距离**、**裁切后距离** 与可见分段数；
   蓝色为可见轨迹，红色虚线为被隐藏段。
5. **下载**：点击"下载遮蔽后的 GPX"，得到一条 `trk` 下多个互不连接的 `trkseg`。

## 分段配速规则（实现要点）

- **按原始 trkseg 分别累计**：每个原始 `trkseg` 独立从 0 起算 haversine 球面距离，
  配速段不跨分段边界合并；trkseg 之间的时间间隔不计入耗时。
- **边界时间插值**：采样线段跨越一个或多个里程边界时，边界时间按**距离比例**在线段
  起止时间之间线性插值。
- **末尾不足一段也列出**：剩余不足一个整段的部分作为最后一段给出实际距离与配速。
- **时间不递增**：同一 trkseg 内相邻轨迹点时间相同或倒退时无法插值，接口返回 400
  并在页面提示"无法计算配速"。
- **零里程段**：距离为 0 的段（如整段原地不动）配速显示为 "—"，不显示无穷大。

## 裁切规则（实现要点）

- **穿越识别**：对每个原始采样线段与每个遮蔽圆求解析交点（局部切平面投影中解二次方程），
  即使相邻两个采样点都在圆外、但线段穿越圆，也会被识别并裁开。
- **时间插值**：交点处的时间（及海拔）按交点在线段上的比例 `t` 线性插值，
  导出时精确到毫秒。
- **分段边界**：可见轨迹不跨原始 `trkseg` 边界合并；每段连续可见部分导出为一个独立
  `trkseg`，隐藏部分不出现在导出文件中。
- **距离统计**：裁切前/后距离均用 haversine 大地线距离累计，裁切后距离包含插值边界点。
- **精度说明**：遮蔽判定在圆心切平面投影中进行，常用半径（数十米至数公里）下与大地线
  的偏差为亚厘米级；导出的边界点经纬度保留 7 位小数（约 1 cm）。

## 项目结构

```
app/
  main.py          # FastAPI 路由：/api/upload、/api/mask、/api/export、/api/pace、静态页面
  geo.py           # 几何核心：haversine、线段-圆求交、按可见性裁切
  pace.py          # 分段配速：按固定里程切段、边界时间插值
  gpxutil.py       # GPX 1.1 解析（强制要求 time）与多 trkseg 导出
  static/          # 原生 JS 前端（index.html / app.js / style.css）
tests/             # 单元测试：test_geo.py（裁切 9 项）、test_pace.py（配速 8 项）
examples/sample.gpx# 示例轨迹（两个 trkseg，含时间）
Dockerfile         # python:3.12-slim，构建期安装依赖，启动即运行
docker-compose.yml # 单服务，映射 8000 端口
requirements.txt
```

## 本地开发（不用 Docker）

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 运行单元测试（核心逻辑不依赖 FastAPI）
python3 tests/test_geo.py       # 裁切逻辑
python3 tests/test_pace.py      # 分段配速
# 或：python3 -m pytest tests/
```

## API 摘要

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/upload` | 表单上传 GPX，返回 `track_id`、轨迹点与统计 |
| `POST` | `/api/mask` | `{track_id, circles:[{lat,lon,radius_m}]}` → 可见/隐藏分段、隐藏段数、裁切前后距离 |
| `POST` | `/api/export` | 同上参数，返回多 `trkseg` 的 GPX 文件下载 |
| `POST` | `/api/pace` | `{track_id, split_km?}` → 分段配速（见下） |

### `POST /api/pace` 分段配速

- **参数**：`track_id`（必填，上传返回值）；`split_km`（可选，每段里程，**0.1～10 公里**，
  默认 **1**，超出范围返回 422）。
- **返回**：按原始 `trkseg` 分组的段列表，每段含 `distance_m`（距离）、`duration_s`（耗时）、
  `pace_min_per_km`（分/公里配速，零里程段为 `null`）、`start_time` / `end_time`
  （ISO 时间，段边界为插值时刻）；另附全程合计 `totals`。
- **错误**：轨迹相邻点时间不递增时返回 `400` 与中文提示；`track_id` 不存在返回 `404`。

示例：

```bash
curl -X POST http://localhost:8000/api/pace \
  -H "Content-Type: application/json" \
  -d '{"track_id": "<上传返回的 track_id>", "split_km": 0.5}'
```
