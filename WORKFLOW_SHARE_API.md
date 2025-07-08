# 工作流分享 API 文档

## 概述

工作流分享功能允许用户分享和复用工作流配置，支持工作流的发布、查询、复用和删除等操作。

## 数据模型

### WorkflowShare 表结构

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | Integer | 主键ID |
| share_title | String | 分享标题（必填，索引） |
| share_comment | String | 分享介绍 |
| share_user | String | 分享人 |
| share_uid | String | 分享人唯一ID |
| name | String | 工作流名称（必填，索引） |
| description | String | 工作流描述 |
| timer | String | 定时器配置 |
| actions | String | 任务列表（JSON字符串） |
| flows | String | 任务流（JSON字符串） |
| context | String | 执行上下文（JSON字符串） |
| date | String | 创建时间（索引） |
| count | Integer | 复用人次（默认0） |

## API 接口

### 1. 新增工作流分享

**接口地址：** `POST /workflow/share`

**请求参数：**

```json
{
    "share_title": "自动化下载工作流",
    "share_comment": "这是一个用于自动下载电影和电视剧的工作流",
    "share_user": "测试用户",
    "share_uid": "test_user_001",
    "name": "自动化下载工作流",
    "description": "自动监控并下载新发布的电影和电视剧",
    "timer": "0 */6 * * *",
    "actions": "[{\"name\":\"检查新发布\",\"type\":\"check_new\"}]",
    "flows": "[{\"from\":\"检查新发布\",\"to\":\"下载文件\",\"condition\":\"has_new\"}]",
    "context": "{\"download_path\":\"/downloads\"}"
}
```

**响应示例：**

```json
{
    "code": 0,
    "message": "success"
}
```

**错误响应：**

```json
{
    "code": 1,
    "message": "请填写分享标题和分享人"
}
```

```json
{
    "code": 2,
    "message": "您使用的昵称已经分享过这个工作流了"
}
```

### 2. 查询工作流分享列表

**接口地址：** `GET /workflow/shares`

**请求参数：**

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| name | String | 否 | 搜索关键词（分享标题或工作流名称） |
| page | Integer | 否 | 页码（默认1） |
| count | Integer | 否 | 每页数量（默认30） |

**响应示例：**

```json
[
    {
        "id": 1,
        "share_title": "自动化下载工作流",
        "share_comment": "这是一个用于自动下载电影和电视剧的工作流",
        "share_user": "测试用户",
        "share_uid": "test_user_001",
        "name": "自动化下载工作流",
        "description": "自动监控并下载新发布的电影和电视剧",
        "timer": "0 */6 * * *",
        "actions": "[{\"name\":\"检查新发布\",\"type\":\"check_new\"}]",
        "flows": "[{\"from\":\"检查新发布\",\"to\":\"下载文件\",\"condition\":\"has_new\"}]",
        "context": "{\"download_path\":\"/downloads\"}",
        "date": "2024-01-01 12:00:00",
        "count": 5
    }
]
```

### 3. 复用工作流

**接口地址：** `GET /workflow/fork/{shareid}`

**路径参数：**

| 参数名 | 类型 | 说明 |
|--------|------|------|
| shareid | Integer | 分享ID |

**响应示例：**

```json
{
    "code": 0,
    "message": "success"
}
```

### 4. 删除工作流分享

**接口地址：** `DELETE /workflow/share/{sid}`

**路径参数：**

| 参数名 | 类型 | 说明 |
|--------|------|------|
| sid | Integer | 分享ID |

**查询参数：**

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| share_uid | String | 是 | 分享人唯一ID（用于权限验证） |

**响应示例：**

```json
{
    "code": 0,
    "message": "success"
}
```

**错误响应：**

```json
{
    "code": 1,
    "message": "分享不存在或无权限"
}
```

## 使用示例

### Python 示例

```python
import requests
import json

# 新增工作流分享
workflow_data = {
    "share_title": "自动化下载工作流",
    "share_comment": "这是一个用于自动下载电影和电视剧的工作流",
    "share_user": "测试用户",
    "share_uid": "test_user_001",
    "name": "自动化下载工作流",
    "description": "自动监控并下载新发布的电影和电视剧",
    "timer": "0 */6 * * *",
    "actions": json.dumps([
        {"name": "检查新发布", "type": "check_new"},
        {"name": "下载文件", "type": "download"},
        {"name": "通知用户", "type": "notify"}
    ]),
    "flows": json.dumps([
        {"from": "检查新发布", "to": "下载文件", "condition": "has_new"},
        {"from": "下载文件", "to": "通知用户", "condition": "download_success"}
    ]),
    "context": json.dumps({
        "download_path": "/downloads",
        "notification_method": "email"
    })
}

response = requests.post("http://localhost:3001/workflow/share", json=workflow_data)
print(response.json())

# 查询工作流分享列表
response = requests.get("http://localhost:3001/workflow/shares")
shares = response.json()
print(shares)

# 复用工作流
if shares:
    share_id = shares[0]['id']
    response = requests.get(f"http://localhost:3001/workflow/fork/{share_id}")
    print(response.json())
```

### cURL 示例

```bash
# 新增工作流分享
curl -X POST "http://localhost:3001/workflow/share" \
  -H "Content-Type: application/json" \
  -d '{
    "share_title": "自动化下载工作流",
    "share_comment": "这是一个用于自动下载电影和电视剧的工作流",
    "share_user": "测试用户",
    "share_uid": "test_user_001",
    "name": "自动化下载工作流",
    "description": "自动监控并下载新发布的电影和电视剧",
    "timer": "0 */6 * * *",
    "actions": "[{\"name\":\"检查新发布\",\"type\":\"check_new\"}]",
    "flows": "[{\"from\":\"检查新发布\",\"to\":\"下载文件\",\"condition\":\"has_new\"}]",
    "context": "{\"download_path\":\"/downloads\"}"
  }'

# 查询工作流分享列表
curl -X GET "http://localhost:3001/workflow/shares"

# 复用工作流
curl -X GET "http://localhost:3001/workflow/fork/1"

# 删除工作流分享
curl -X DELETE "http://localhost:3001/workflow/share/1?share_uid=test_user_001"
```

## 注意事项

1. **权限控制**：删除工作流分享需要提供正确的 `share_uid` 进行权限验证
2. **数据格式**：`actions`、`flows`、`context` 字段需要以 JSON 字符串格式存储
3. **缓存机制**：系统使用缓存来提高查询性能，缓存时间为30分钟
4. **唯一性约束**：同一用户不能重复分享相同标题的工作流
5. **分页查询**：列表查询支持分页，默认每页30条记录

## 错误码说明

| 错误码 | 说明 |
|--------|------|
| 0 | 成功 |
| 1 | 参数错误或权限不足 |
| 2 | 重复分享错误 |