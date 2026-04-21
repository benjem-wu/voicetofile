# 数据获取

## URL 清单

| 用途 | URL |
|------|-----|
| 播客主页（获取名称 + 15 集列表） | `https://www.xiaoyuzhoufm.com/podcast/{pid}` |
| 单集 JSON 数据（优先） | `https://www.xiaoyuzhoufm.com/_next/data/-GOav0dS9wDlfSnB05lx2/episode/{eid}.json` |
| 单集 HTML 详情（备选） | `https://www.xiaoyuzhoufm.com/episode/{eid}` |
| 音频文件 | `https://media.xyzcdn.net/{pid}/{hash}.m4a` |

---

## 列表页解析方式（关键经验）

**`xyzcdn.net` 不能用**——它返回的 JSON-LD 里 `workExample` 有 15 集数据，但**不含 eid**。

正确方式：从 `xiaoyuzhoufm.com/podcast/{pid}` HTML 中提取 **JavaScript 内嵌数据**：
```
"episodes":[{"type":"EPISODE","eid":"69de4c4ab977fb2c47ef785e",
  "pid":"...","title":"...","description":"...",
  "duration":"PT8M38S","pubDate":"..."}]
```
- **eid、title、pubDate** 从 HTML JavaScript 数据提取（regex 逐字段安全提取）
- **description、duration** 从 JSON-LD 的 `workExample` 补充（通过 name 关联）
- 两者缺一不可，JSON-LD 无 eid，JavaScript 数据无完整 description

---

## 单集音频 URL

从 JSON-LD `associatedMedia.contentUrl` 或 HTML JSON-LD `enclosure.url` 提取。

---

## 列表页正则提取策略

```python
# 阶段1：从 JavaScript 提取所有 eid / title / pubDate（逐字段，避免对象解析）
eids = re.findall(r'"eid"\s*:\s*"([a-f0-9]{20,})"', chunk)
titles = re.findall(r'"title"\s*:\s*"([^"]*)"', chunk)
pubdates = re.findall(r'"pubDate"\s*:\s*"([^"]*)"', chunk)

# 阶段2：从 JSON-LD 补充 description / duration（通过 name 关联）
```
