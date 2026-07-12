import { createFileRoute } from "@tanstack/react-router";
import { useState, useMemo } from "react";
import { motion } from "framer-motion";
import {
  Copy,
  Download,
  User,
  Briefcase,
  Calendar,
  CheckCircle2,
  Sparkles,
} from "lucide-react";
import {
  GlassCard,
  PageHeader,
  LoadingSpinner,
  ErrorDisplay,
  EmptyState,
  AnimeButton,
  StatusBadge,
} from "@/components/ui";
import { useCharacters, useCharacter } from "@/lib/queries";
import type { Character } from "@/lib/api";

export const Route = createFileRoute("/character-card")({
  component: CharacterCardPage,
});

// 将值转换为 YAML 友好的字符串表示
function yamlValue(val: unknown, indent = 0): string {
  const pad = "  ".repeat(indent);
  if (val === null || val === undefined) return '""';
  if (typeof val === "string") {
    // 含特殊字符的字符串加引号
    if (/[:#\[\]{}&*!|>'"%@`]/.test(val) || val.includes("\n")) {
      return `"${val.replace(/"/g, '\\"')}"`;
    }
    return `"${val}"`;
  }
  if (typeof val === "number" || typeof val === "boolean") return String(val);
  if (Array.isArray(val)) {
    if (val.length === 0) return "[]";
    return val
      .map((v) => {
        if (typeof v === "string") return `${pad}  - "${v}"`;
        return `${pad}  - ${yamlValue(v, indent + 1)}`;
      })
      .join("\n");
  }
  if (typeof val === "object") {
    const entries = Object.entries(val as Record<string, unknown>);
    if (entries.length === 0) return "{}";
    return entries
      .map(([k, v]) => {
        if (typeof v === "object" && v !== null) {
          return `${pad}${k}:\n${yamlValue(v, indent + 1)}`;
        }
        return `${pad}${k}: ${yamlValue(v, indent)}`;
      })
      .join("\n");
  }
  return String(val);
}

// 将角色对象转换为 YAML 格式字符串
function characterToYaml(char: Character): string {
  const lines: string[] = [];
  lines.push(`# AI Town 角色卡`);
  lines.push(`name: "${char.name}"`);
  lines.push(`id: "${char.id}"`);
  lines.push(`age: ${char.age ?? '""'}`);
  lines.push(`occupation: "${char.occupation ?? ""}"`);
  lines.push(`is_active: ${char.is_active}`);
  lines.push(`avatar_url: "${char.avatar_url ?? ""}"`);

  // traits 处理
  if (char.traits && Object.keys(char.traits).length > 0) {
    lines.push("traits:");
    for (const [key, value] of Object.entries(char.traits)) {
      if (Array.isArray(value)) {
        lines.push(`  ${key}:`);
        for (const item of value) {
          lines.push(`    - "${String(item)}"`);
        }
      } else if (typeof value === "object" && value !== null) {
        lines.push(`  ${key}:`);
        for (const [subKey, subVal] of Object.entries(
          value as Record<string, unknown>,
        )) {
          lines.push(`    ${subKey}: ${yamlValue(subVal)}`);
        }
      } else {
        lines.push(`  ${key}: ${yamlValue(value)}`);
      }
    }
  } else {
    lines.push("traits:");
    lines.push("  personality: []");
  }

  // backstory 处理（多行文本用 | 块标记）
  if (char.backstory) {
    lines.push("backstory: |");
    const storyLines = char.backstory.split("\n");
    for (const line of storyLines) {
      lines.push(`  ${line}`);
    }
  } else {
    lines.push('backstory: ""');
  }

  return lines.join("\n") + "\n";
}

function CharacterCardPage() {
  // 获取角色列表用于下拉选择
  const { data: charactersData, isLoading: charsLoading } = useCharacters();
  const characters = charactersData?.data ?? [];

  // 当前选中的角色 ID
  const [characterId, setCharacterId] = useState<string>("");
  // 复制成功提示
  const [copied, setCopied] = useState(false);

  // 获取角色详情
  const { data: character, isLoading, error } = useCharacter(characterId);

  // 生成 YAML 字符串
  const yamlContent = useMemo(() => {
    if (!character) return "";
    return characterToYaml(character);
  }, [character]);

  // 从 traits 中提取性格标签列表
  const personalityTags = useMemo(() => {
    if (!character?.traits) return [];
    const traits = character.traits;
    // 优先取 personality 字段
    if (Array.isArray(traits.personality)) {
      return traits.personality as string[];
    }
    // 兜底：合并所有数组类型的 trait
    const all: string[] = [];
    for (const v of Object.values(traits)) {
      if (Array.isArray(v)) {
        all.push(...(v as string[]));
      }
    }
    return all;
  }, [character]);

  // 复制 YAML 到剪贴板
  const handleCopyYaml = async () => {
    if (!yamlContent) return;
    try {
      await navigator.clipboard.writeText(yamlContent);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // 剪贴板不可用时静默失败
    }
  };

  // 下载 YAML 文件
  const handleDownloadYaml = () => {
    if (!yamlContent || !character) return;
    const safeName = character.name.replace(/[^\w\u4e00-\u9fa5-]/g, "_");
    const blob = new Blob([yamlContent], { type: "text/yaml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${safeName}.yaml`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
      <div className="space-y-6 animate-fade-in-up">
        <PageHeader
          title="角色卡预览"
          subtitle="查看角色卡 YAML 格式与可视化卡片"
          icon="🎴"
        />

        {/* 角色选择器 */}
        <GlassCard hover={false}>
          <div className="space-y-2">
            <label className="text-sm font-medium text-twilight-500 flex items-center gap-1.5">
              <User className="w-4 h-4 text-sakura-400" />
              选择角色
            </label>
            <select
              value={characterId}
              onChange={(e) => setCharacterId(e.target.value)}
              disabled={charsLoading}
              className="w-full px-4 py-3 rounded-xl bg-white/60 border border-sakura-200/60 text-twilight-700 focus:outline-none focus:ring-2 focus:ring-sakura-400/50 focus:border-transparent focus:bg-white/80 transition-all disabled:opacity-50"
            >
              <option value="">
                {charsLoading ? "加载角色中..." : "请选择角色"}
              </option>
              {characters.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}（{c.id}）
                </option>
              ))}
            </select>
          </div>
        </GlassCard>

        {/* 未选择角色提示 */}
        {!characterId && !charsLoading && (
          <EmptyState
            icon="🎴"
            title="请先选择一个角色"
            subtitle="选择角色后将显示其角色卡 YAML 与可视化预览"
          />
        )}

        {/* 加载与错误状态 */}
        {characterId && isLoading && <LoadingSpinner text="正在加载角色详情..." />}
        {characterId && error && <ErrorDisplay error={error} />}

        {character && !isLoading && !error && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="grid lg:grid-cols-2 gap-6"
          >
            {/* 左侧：YAML 代码块 */}
            <GlassCard hover={false} className="space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold text-sakura-600 flex items-center gap-2 text-lg">
                  <Sparkles className="w-5 h-5" />
                  YAML 角色卡
                </h3>
                <div className="flex items-center gap-2">
                  <AnimeButton
                    onClick={handleCopyYaml}
                    variant="secondary"
                    className="!px-3 !py-1.5 !text-sm"
                  >
                    <span className="flex items-center gap-1.5">
                      {copied ? (
                        <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" />
                      ) : (
                        <Copy className="w-3.5 h-3.5" />
                      )}
                      {copied ? "已复制" : "复制 YAML"}
                    </span>
                  </AnimeButton>
                  <AnimeButton
                    onClick={handleDownloadYaml}
                    variant="secondary"
                    className="!px-3 !py-1.5 !text-sm"
                  >
                    <span className="flex items-center gap-1.5">
                      <Download className="w-3.5 h-3.5" />
                      下载 YAML
                    </span>
                  </AnimeButton>
                </div>
              </div>

              {/* YAML 代码展示 */}
              <pre className="p-4 rounded-2xl bg-twilight-900/90 text-sakura-100 text-xs font-mono overflow-x-auto max-h-[480px] overflow-y-auto leading-relaxed">
                {yamlContent}
              </pre>
            </GlassCard>

            {/* 右侧：角色卡可视化预览 */}
            <GlassCard hover={false} className="space-y-5">
              <h3 className="font-semibold text-sakura-600 flex items-center gap-2 text-lg">
                <User className="w-5 h-5" />
                角色卡预览
              </h3>

              {/* 角色卡主体 */}
              <div className="relative overflow-hidden rounded-3xl bg-gradient-to-br from-sakura-100/60 via-white/50 to-twilight-100/60 border border-white/50 p-6 shadow-soft">
                {/* 装饰光晕 */}
                <div className="absolute -top-8 -right-8 w-32 h-32 rounded-full bg-sakura-200/30 blur-2xl" />
                <div className="absolute -bottom-8 -left-8 w-32 h-32 rounded-full bg-twilight-200/30 blur-2xl" />

                {/* 头部：头像 + 名称 */}
                <div className="relative flex items-center gap-4">
                  {/* 头像占位 */}
                  <div className="w-20 h-20 rounded-3xl bg-gradient-to-br from-sakura-300 via-sakura-400 to-twilight-300 flex items-center justify-center text-white font-bold text-3xl shadow-lg shrink-0">
                    {character.name[0]}
                  </div>
                  <div className="min-w-0">
                    <h4 className="text-2xl font-bold gradient-text-sakura truncate">
                      {character.name}
                    </h4>
                    <div className="flex items-center gap-2 mt-1 flex-wrap">
                      <StatusBadge
                        status={character.is_active ? "ok" : "idle"}
                        label={character.is_active ? "活跃" : "休眠"}
                      />
                      {character.occupation && (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-sky-soft-100/80 text-sky-soft-600 border border-sky-soft-200/50">
                          <Briefcase className="w-3 h-3" />
                          {character.occupation}
                        </span>
                      )}
                    </div>
                  </div>
                </div>

                {/* 基本信息 */}
                <div className="relative mt-5 grid grid-cols-2 gap-3">
                  <div className="flex items-center gap-2 p-2.5 rounded-xl bg-white/50 border border-white/40">
                    <Calendar className="w-4 h-4 text-twilight-400 shrink-0" />
                    <div className="min-w-0">
                      <div className="text-xs text-twilight-400">年龄</div>
                      <div className="text-sm font-medium text-twilight-600">
                        {character.age ? `${character.age} 岁` : "—"}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 p-2.5 rounded-xl bg-white/50 border border-white/40">
                    <User className="w-4 h-4 text-twilight-400 shrink-0" />
                    <div className="min-w-0">
                      <div className="text-xs text-twilight-400">ID</div>
                      <div className="text-sm font-medium text-twilight-600 font-mono truncate">
                        {character.id}
                      </div>
                    </div>
                  </div>
                </div>

                {/* 性格标签 */}
                {personalityTags.length > 0 && (
                  <div className="relative mt-4">
                    <div className="text-xs font-semibold text-twilight-500 uppercase tracking-wide mb-2">
                      性格标签
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {personalityTags.map((tag, idx) => (
                        <motion.span
                          key={`${tag}-${idx}`}
                          initial={{ opacity: 0, scale: 0.8 }}
                          animate={{ opacity: 1, scale: 1 }}
                          transition={{ delay: idx * 0.05 }}
                          className="px-2.5 py-1 rounded-full text-xs font-medium bg-gradient-to-r from-sakura-100 to-twilight-100 text-sakura-600 border border-sakura-200/50"
                        >
                          {tag}
                        </motion.span>
                      ))}
                    </div>
                  </div>
                )}

                {/* 背景故事 */}
                {character.backstory && (
                  <div className="relative mt-4">
                    <div className="text-xs font-semibold text-twilight-500 uppercase tracking-wide mb-2">
                      背景故事
                    </div>
                    <p className="text-sm text-twilight-600 leading-relaxed bg-white/40 rounded-xl p-3 border border-white/40 whitespace-pre-wrap">
                      {character.backstory}
                    </p>
                  </div>
                )}
              </div>
            </GlassCard>
          </motion.div>
        )}
      </div>
  );
}
