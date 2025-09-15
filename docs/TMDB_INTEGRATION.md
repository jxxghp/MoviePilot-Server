# TheMovieDB集成功能

## 功能说明

订阅统计和订阅分享新增接口现在支持自动从TheMovieDB获取genre_id信息。

## 配置

系统已内置默认的TheMovieDB API密钥 (`db55323b8d3e4154498498a75642b381`)，无需额外配置即可使用。

如果需要使用自己的API密钥，可以在环境变量或`.env`文件中设置：

```bash
TMDB_API_KEY=your_custom_tmdb_api_key_here
```

## 功能特性

### Genre IDs格式说明

TheMovieDB API返回的genres信息格式：
- **API响应**：`genres` 数组，每个元素包含 `id` 和 `name` 字段
- **存储格式**：提取所有 `id` 并用逗号连接，如 `"18,53"`（对应 Drama, Thriller）
- **示例**：电影《搏击俱乐部》的genre_ids为 `"18,53"`

### 1. 订阅统计接口 (`/subscribe/add`)

当添加订阅统计时，如果：
- 没有提供`genre_ids`
- 但提供了`tmdbid`和`type`

系统会自动调用TheMovieDB API获取：
- `genre_ids` - 类型ID列表（逗号分隔的字符串格式，如 "18,53"）
- `name` - 媒体名称（如果缺失）
- `year` - 年份（如果缺失）
- `poster` - 海报URL（如果缺失）
- `backdrop` - 背景图URL（如果缺失）
- `vote` - 评分（如果缺失）
- `description` - 简介（如果缺失）

### 2. 订阅分享接口 (`/subscribe/share`)

当创建订阅分享时，如果：
- 没有提供`genre_ids`
- 但提供了`tmdbid`和`type`

系统会自动调用TheMovieDB API获取上述信息（包括逗号分隔的genre_ids格式）。

## API接口

### TheMovieDB服务类

```python
from app.services.tmdb import tmdb_service

# 获取媒体完整信息
media_info = await tmdb_service.get_media_info(tmdb_id, media_type)

# 仅获取genre_ids
genre_ids = await tmdb_service.get_genre_ids(tmdb_id, media_type)
```

## 错误处理

- 如果TheMovieDB API调用失败，不会影响正常的订阅/分享流程
- 错误信息会记录到控制台日志中
- 系统会继续使用原始数据创建记录

## 依赖

新增依赖包：
- `aiohttp>=3.8.0` - 用于异步HTTP请求

## 测试

系统已配置默认API密钥，可以直接使用TheMovieDB集成功能，无需额外配置。