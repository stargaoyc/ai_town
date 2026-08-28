"""RBAC 角色常量 - 角色词表的单一真相源

独立成模块是为了打破循环导入：middleware（凭证解析）与 rbac（授权判定）
都需要在不发生相互依赖的前提下引用同一套角色取值。

角色 → 权限的映射策略在 rbac.py；本模块只定义「有哪些角色」。
角色分配来自 settings.rbac_roles（"user:role,user:role"），
由 jwt_handler.resolve_role 解析后写入 JWT claim。
"""

ROLE_VIEWER = "viewer"
ROLE_OPERATOR = "operator"
ROLE_ADMIN = "admin"

# 全部合法角色：用于启动期校验配置取值，拼错的角色会静默降级为 viewer，
# 届时「配了 admin 却没有权限」极难排查
ALL_ROLES = frozenset({ROLE_VIEWER, ROLE_OPERATOR, ROLE_ADMIN})
