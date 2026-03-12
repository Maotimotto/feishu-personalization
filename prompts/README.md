# 提示词模板说明

## 目录结构

```
prompts/
├── system_prompt.md      # 系统提示词（角色定义 + 任务流程 + 推荐逻辑 + 合规要求）
├── output_format.md      # 输出格式模板
├── profiles/             # 达人画像（按需取用）
│   ├── trader.md         # Trader 达人画像
│   └── feige.md          # 飞哥说财 达人画像
└── README.md             # 本文件
```

## 分类说明

| 文件 | Prompt 类型 | 说明 |
|---|---|---|
| `system_prompt.md` | System Prompt | AI 的角色、任务流程、推荐逻辑、合规要求 |
| `output_format.md` | System Prompt（输出约束） | 定义 AI 输出的 Markdown 结构 |
| `profiles/*.md` | Human Prompt（变量） | 达人人物画像，每次调用按目标达人选取 |
| 今日新闻数据 | Human Prompt（变量） | 每日动态抓取注入，不存储在此 |

## 模板拼接方式

最终发送给 LLM 的提示词按以下模板拼接：

```
<instructions>
{system_prompt.md 内容}
</instructions>

<creator_profile>
{选中的 profiles/xxx.md 内容}
</creator_profile>

<output_format>
{output_format.md 内容}
</output_format>

<headlines>
{当日抓取的新闻数据}
</headlines>
```

## 新增达人

在 `profiles/` 目录下新建 `.md` 文件，参考现有画像格式填写即可。
