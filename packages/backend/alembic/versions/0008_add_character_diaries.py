"""新增 character_diaries 与 person_memories 表

变更内容：
1. 创建 character_diaries 表 - 角色日记（基于 memory_episodes 生成的叙事归档）
2. 创建 person_memories 表 - 角色对用户的独立记忆（跨会话长期认知）
3. 分别创建索引（时间线查询、周期查询、角色+用户唯一约束、热度查询）

原因：
- character_diaries: 提供角色视角的叙事归档层，不替代 Episode 真相源，
  支持 day/week/month/year 四种周期生成日记。
- person_memories: 记录角色对每个用户的长期认知（偏好、关系进展、共同话题），
  每次交互后更新，影响后续对话上下文。与 conversation.context（会话级短期摘要）互补。

注意：降级脚本仅 raise RuntimeError，遵循"upgrade only"原则。
"""
import sqlalchemy as sa
from alembic import op

revision = "add_char_diaries"
down_revision = "0007_character_state_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. 创建 character_diaries 表
    op.execute("""
        CREATE TABLE IF NOT EXISTS character_diaries (
            id UUID NOT NULL DEFAULT uuidv7(),
            character_id UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
            period VARCHAR(20) NOT NULL,
            diary_date TIMESTAMPTZ NOT NULL,
            diary_end_date TIMESTAMPTZ,
            title VARCHAR(200),
            content TEXT NOT NULL,
            mood VARCHAR(50),
            generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id)
        )
    """)

    # 2. character_diaries 索引
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_diary_char_date
        ON character_diaries (character_id, diary_date)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_diary_char_period
        ON character_diaries (character_id, period, diary_date)
    """)

    # 3. character_diaries 列注释
    op.execute("COMMENT ON TABLE character_diaries IS '角色日记 - 基于 memory_episodes 生成的叙事归档层'")
    op.execute("COMMENT ON COLUMN character_diaries.id IS '日记 ID（UUID v7，时间有序）'")
    op.execute("COMMENT ON COLUMN character_diaries.character_id IS '角色 ID'")
    op.execute("COMMENT ON COLUMN character_diaries.period IS '周期类型 day/week/month/year'")
    op.execute("COMMENT ON COLUMN character_diaries.diary_date IS '日记日期'")
    op.execute("COMMENT ON COLUMN character_diaries.diary_end_date IS '周期结束日期（day 类型为空，其他为周期起始）'")
    op.execute("COMMENT ON COLUMN character_diaries.title IS '日记标题'")
    op.execute("COMMENT ON COLUMN character_diaries.content IS '日记内容（叙事性正文）'")
    op.execute("COMMENT ON COLUMN character_diaries.mood IS '日记时的情绪'")
    op.execute("COMMENT ON COLUMN character_diaries.generated_at IS '生成时间'")

    # 4. 创建 person_memories 表
    op.execute("""
        CREATE TABLE IF NOT EXISTS person_memories (
            id UUID NOT NULL DEFAULT uuidv7(),
            character_id UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
            user_id VARCHAR(100) NOT NULL,
            platform VARCHAR(20) NOT NULL DEFAULT 'web',
            content TEXT NOT NULL,
            summary TEXT,
            heat INTEGER NOT NULL DEFAULT 0,
            last_interaction_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            preferences JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id)
        )
    """)

    # 5. person_memories 索引
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pmem_char_user
        ON person_memories (character_id, user_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_pmem_heat
        ON person_memories (character_id, heat)
    """)

    # 6. person_memories 列注释
    op.execute("COMMENT ON TABLE person_memories IS '角色对用户的独立记忆（跨会话长期认知）'")
    op.execute("COMMENT ON COLUMN person_memories.id IS '记忆 ID（UUID v7，时间有序）'")
    op.execute("COMMENT ON COLUMN person_memories.character_id IS '角色 ID'")
    op.execute("COMMENT ON COLUMN person_memories.user_id IS '用户标识（如 qq_123456）'")
    op.execute("COMMENT ON COLUMN person_memories.platform IS '来源平台（web/qq/lark/internal）'")
    op.execute("COMMENT ON COLUMN person_memories.content IS '记忆内容（自然语言描述）'")
    op.execute("COMMENT ON COLUMN person_memories.summary IS '压缩摘要'")
    op.execute("COMMENT ON COLUMN person_memories.heat IS '热度（交互次数）'")
    op.execute("COMMENT ON COLUMN person_memories.last_interaction_at IS '最后交互时间'")
    op.execute("COMMENT ON COLUMN person_memories.preferences IS '用户偏好（结构化）'")
    op.execute("COMMENT ON COLUMN person_memories.created_at IS '创建时间'")
    op.execute("COMMENT ON COLUMN person_memories.updated_at IS '更新时间'")


def downgrade() -> None:
    raise RuntimeError("Downgrade not supported. Follow upgrade-only principle.")
