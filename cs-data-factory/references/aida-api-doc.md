# 知识库工作流 API 协议示例

> **API 服务器**: `https://knowledge-api.example.com/v1`
> **应用类型**: 工作流应用

## 对话模式

- **并行对话**：调用方传入的消息会被并行执行，按结果生成的先后顺序返回给调用方。
- **会话打断**：调用方传入新消息后，将会把生成中的消息打断，将打断的消息与新消息拼接后继续生成结果，返回给调用方。

---

## 1. 发送对话消息

```
POST /chat-messages
```

创建会话消息，或基于此前的对话继续发送消息。

### Request Body

| Name | Type | Description |
|---|---|---|
| `app_id` | string | 由使用者的知识库服务提供，不在 Skill 中预置 |
| `inputs` | object | （选填）以键值对方式提供用户输入字段，与提示词编排中的变量对应。Key 为变量名称，Value 是参数值。如果字段类型为 Select，传入的 Value 需为预设选项之一。 |
| `query` | string | 用户输入/提问内容 |
| `response-mode` | string | `blocking` 阻塞型，等待执行完毕后返回结果（流程较长可能会被中断）；`streaming` 流式返回，基于 SSE 实现 |
| `conversation_id` | string | （选填）会话标识符，首次对话可为空，继续对话请传入上下文返回的 conversation_id |
| `user` | string | 用户标识，由开发者定义规则，需保证用户标识在应用内唯一 |

### Request 示例

```bash
curl --location --request POST 'https://knowledge-api.example.com/v1/chat-messages' \
--header 'Authorization: Bearer <API_KEY>' \
--header 'Content-Type: application/json' \
--data-raw '{
    "app_id":"ENTER-YOUR-APP-ID",
    "inputs": {},
    "query": "eh",
    "response_mode": "streaming",
    "user": "abc-123",
    "conversation_id": "1c7e55fb-1ba2-4e10-81b5-30addcea2276"
}'
```

### Response（blocking）

```json
{
  "answer": "Hi, is there anything I can help you?",
  "conversation_id": "45701982-8118-4bc5-8e9b-64562b4555f2",
  "created_at": 1679587005,
  "id": "059f87d9-15c0-473a-870c-fde95cdcc1e4"
}
```

### Response（streaming）

```
data: {"id": "5ad4cb98-f0c7-4085-b384-88c403be6290", "answer": " I", "created_at": 1679586595}
data: {"id": "5ad4cb98-f0c7-4085-b384-88c403be6290", "answer": " I", "created_at": 1679586595}
```

---

## 2. 消息反馈（点赞）

```
POST /messages/{message_id}/feedbacks
```

代表最终用户对返回消息进行评价，可以点赞与点踩，该数据将在"日志与标注"页中可见，并用于后续的模型微调。

### Path Params

| Name | Type | Description |
|---|---|---|
| `message_id` | string | 消息 ID |

### Request Body

| Name | Type | Description |
|---|---|---|
| `rating` | string | `like` 或 `dislike`，空值为撤销 |
| `user` | string | 用户标识，需保证在应用内唯一 |

### Request 示例

```bash
curl --location --request POST 'https://knowledge-api.example.com/v1/messages/{message_id}/feedbacks' \
--header 'Authorization: Bearer <API_KEY>' \
--header 'Content-Type: application/json' \
--data-raw '{
    "rating": "like",
    "user": "abc-123"
}'
```

### Response

```json
{
  "has_more": false,
  "data": [
    {
      "id": "WAz8eIbvDR60rouK",
      "username": "FrankMcCallister",
      "conversation_id": "xgQQXg3hrtjh7AvZ",
      "last_active_at": 705103200,
      "created_at": 692233200
    }
  ]
}
```

---

## 3. 获取会话历史消息

```
GET /messages
```

滚动加载形式返回历史聊天记录，第一页返回最新 limit 条，即：倒序返回。

### Query Params

| Name | Type | Description |
|---|---|---|
| `conversation-id` | string | 会话 ID |
| `first_id` | string | 当前页第一条聊天记录的 ID，默认 none |
| `limit` | int | 一次请求返回多少条聊天记录 |
| `user` | string | 用户标识，需保证在应用内唯一 |

### Request 示例

```bash
curl --location --request GET 'https://knowledge-api.example.com/v1/messages?user=abc-123&conversation_id=' \
--header 'Authorization: Bearer <API_KEY>'
```

### Response

```json
{
  "has_more": false,
  "data": [
    {
      "id": "WAz8eIbvDR60rouK",
      "username": "FrankMcCallister",
      "conversation_id": "xgQQXg3hrtjh7AvZ",
      "last_active_at": 705103200,
      "created_at": 692233200
    }
  ]
}
```

---

## 4. 获取会话列表

```
GET /conversations
```

获取当前用户的会话列表，默认返回最近的 20 条。

### Query Params

| Name | Type | Description |
|---|---|---|
| `last_id` | string | 当前页最后面一条记录的 ID，默认 none |
| `limit` | int | 一次请求返回多少条记录 |
| `user` | string | 用户标识，需保证在应用内唯一 |

### Request 示例

```bash
curl --location --request GET 'https://knowledge-api.example.com/v1/conversations?user=abc-123&last_id=&limit=20' \
--header 'Authorization: Bearer <API_KEY>'
```

### Response

```json
{
  "limit": 20,
  "has_more": false,
  "data": [
    {
      "id": "10799fb8-64f7-4296-bbf7-b42bfbe0ae54",
      "name": "New rule",
      "inputs": {
        "book": "book",
        "myName": "Lucy"
      },
      "status": "normal",
      "created_at": 1679667915
    }
  ]
}
```

---

## 5. 会话重命名

```
POST /conversations/{converation-id}/name
```

对会话进行重命名，会话名称用于显示在支持多会话的客户端上。

### Request Body

| Name | Type | Description |
|---|---|---|
| `name` | string | 新的名称 |
| `user` | string | 用户标识，需保证在应用内唯一 |

### Request 示例

```bash
curl --location --request POST 'https://knowledge-api.example.com/v1/conversations/{converation_id}/name' \
--header 'Authorization: Bearer <API_KEY>' \
--header 'Content-Type: application/json' \
--data-raw '{
  "name": "",
  "user": "abc-123"
}'
```

### Response

```json
{
  "result": "success"
}
```

---

## 6. 删除会话

```
DELETE /conversations/{converation_id}
```

### Request Body

| Name | Type | Description |
|---|---|---|
| `user` | string | 用户标识，需保证在应用内唯一 |

### Request 示例

```bash
curl --location --request DELETE 'https://knowledge-api.example.com/v1/conversations/{converation_id}' \
--header 'Authorization: Bearer <API_KEY>' \
--header 'Content-Type: application/json' \
--data-raw '{
  "user": "abc-123"
}'
```

### Response

```json
{
  "result": "success"
}
```

---

## 7. 获取应用配置信息

```
GET /parameters
```

获取已配置的 Input 参数，包括变量名、字段名称、类型与默认值。通常用于客户端加载后显示这些字段的表单或填入默认值。

### Query Params

| Name | Type | Description |
|---|---|---|
| `user` | string | 用户标识，需保证在应用内唯一 |

### Request 示例

```bash
curl --location --request GET 'https://knowledge-api.example.com/v1/parameters' \
--header 'Authorization: Bearer <API_KEY>'
```

### Response

```json
{
  "introduction": "nice to meet you",
  "variables": [
    {
      "key": "book",
      "name": "book",
      "description": null,
      "type": "string",
      "default": null,
      "options": null
    }
  ]
}
```
