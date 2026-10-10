# 数据库结构入口

当前数据库基线为 **Schema Version 5、28 张表**。

为避免重复文档漂移，本文件只保留导航，不再复制表结构。请按以下优先级阅读：

1. [DATABASE_OVERVIEW.md](../DATABASE_OVERVIEW.md)：数据库总览、表关系和用途；
2. `src/medical_claims/database_migrations.py`：正式 Schema、版本和迁移逻辑的唯一权威来源；
3. `src/medical_claims/storage.py`：运行时数据访问、关联、权限过滤和状态流转。

旧画像兼容三表必须保留：

- `case_profile_contexts`
- `profile_case_records`
- `quality_portfolios`

不得根据旧分支、旧测试或历史文档恢复任何已废弃表。手机预览使用进程内限时缓存，不写入 SQLite。
