---
name: factor-count
description: 用户询问自己的 XYQuant 账号能查看多少个因子时使用。
---

# 查询因子数量

调用 XYQuant 的 `list_factors`，设置 `page=1`、`page_size=1`，省略搜索条件。读取返回的 `total`，回答“当前账号可查看 X 个因子”。
