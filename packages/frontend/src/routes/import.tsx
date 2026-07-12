import { createFileRoute } from "@tanstack/react-router";
import { useState, useRef } from "react";
import { motion } from "framer-motion";
import { Upload, FileText, Download, CheckCircle2, XCircle } from "lucide-react";
import {
  NavLayout,
  GlassCard,
  PageHeader,
  AnimeButton,
  EmptyState,
} from "@/components/ui";
import {
  useImportCharacter,
  useImportCharacterBatch,
} from "@/lib/queries";

export const Route = createFileRoute("/import")({
  component: ImportPage,
});

// 角色卡 YAML 模板示例
const YAML_TEMPLATE = `# AI Town 角色卡模板
name: "樱花酱"
age: 18
occupation: "女高中生"
is_active: true
avatar_url: ""
traits:
  personality: ["温柔", "害羞", "乐观"]
  likes: ["樱花", "甜食", "读书"]
  dislikes: ["孤独", "争吵"]
backstory: |
  樱花酱是一名普通的女高中生，性格温柔害羞，
  喜欢在放学后去图书馆读书。她梦想有一天能环游世界，
  用相机记录下每一个美好的瞬间。
`;

function ImportPage() {
  const [yamlText, setYamlText] = useState("");
  const [result, setResult] = useState<
    { type: "success" | "error"; message: string } | null
  >(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const importSingle = useImportCharacter();
  const importBatch = useImportCharacterBatch();

  // 处理文件上传
  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      const text = ev.target?.result as string;
      setYamlText(text);
      setResult(null);
    };
    reader.readAsText(file);
  };

  // 触发文件选择
  const handleSelectFile = () => {
    fileInputRef.current?.click();
  };

  // 导入单个角色
  const handleImportSingle = () => {
    if (!yamlText.trim()) {
      setResult({ type: "error", message: "请先粘贴或上传 YAML 内容" });
      return;
    }
    setResult(null);
    importSingle.mutate(yamlText, {
      onSuccess: (data) => {
        setResult({
          type: "success",
          message: `角色导入成功！${JSON.stringify(data)}`,
        });
        setYamlText("");
      },
      onError: (err) => {
        setResult({ type: "error", message: err.message });
      },
    });
  };

  // 批量导入
  const handleImportBatch = () => {
    if (!yamlText.trim()) {
      setResult({ type: "error", message: "请先粘贴或上传 YAML 内容" });
      return;
    }
    setResult(null);
    importBatch.mutate(yamlText, {
      onSuccess: (data) => {
        setResult({
          type: "success",
          message: `批量导入成功！${JSON.stringify(data)}`,
        });
        setYamlText("");
      },
      onError: (err) => {
        setResult({ type: "error", message: err.message });
      },
    });
  };

  // 下载模板
  const handleDownloadTemplate = () => {
    const blob = new Blob([YAML_TEMPLATE], { type: "text/yaml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "character-template.yaml";
    a.click();
    URL.revokeObjectURL(url);
  };

  // 加载模板到文本框
  const handleLoadTemplate = () => {
    setYamlText(YAML_TEMPLATE);
    setResult(null);
  };

  const isPending = importSingle.isPending || importBatch.isPending;

  return (
    <NavLayout>
      <div className="space-y-6 animate-fade-in-up">
        <PageHeader
          title="角色导入"
          subtitle="通过 YAML 角色卡导入新角色到小镇"
          icon="📥"
        />

        {/* 操作按钮区 */}
        <GlassCard>
          <div className="flex flex-wrap items-center gap-3">
            <AnimeButton onClick={handleSelectFile} variant="secondary">
              <span className="flex items-center gap-2">
                <Upload className="w-4 h-4" />
                上传 YAML 文件
              </span>
            </AnimeButton>
            <input
              ref={fileInputRef}
              type="file"
              accept=".yaml,.yml"
              onChange={handleFileUpload}
              className="hidden"
            />
            <AnimeButton onClick={handleLoadTemplate} variant="secondary">
              <span className="flex items-center gap-2">
                <FileText className="w-4 h-4" />
                加载模板示例
              </span>
            </AnimeButton>
            <AnimeButton onClick={handleDownloadTemplate} variant="secondary">
              <span className="flex items-center gap-2">
                <Download className="w-4 h-4" />
                下载模板
              </span>
            </AnimeButton>
          </div>
        </GlassCard>

        {/* YAML 编辑区 */}
        <GlassCard>
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold text-sakura-600 flex items-center gap-2">
              <FileText className="w-5 h-5" />
              YAML 角色卡内容
            </h3>
            <span className="text-xs text-twilight-400">
              {yamlText.length} 字符
            </span>
          </div>
          {yamlText ? (
            <textarea
              value={yamlText}
              onChange={(e) => setYamlText(e.target.value)}
              className="w-full h-80 p-4 rounded-2xl bg-white/70 border border-sakura-200/60 text-twilight-700 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-sakura-400/50 focus:border-transparent focus:bg-white/90 transition-all resize-y"
              placeholder="在此粘贴 YAML 格式的角色卡内容..."
              spellCheck={false}
            />
          ) : (
            <div
              onClick={() => setYamlText(YAML_TEMPLATE)}
              className="w-full h-80 p-4 rounded-2xl bg-white/40 border-2 border-dashed border-sakura-200/60 flex items-center justify-center cursor-pointer hover:bg-white/60 hover:border-sakura-300/60 transition-all"
            >
              <EmptyState
                icon="📋"
                title="粘贴或上传 YAML 内容"
                subtitle="点击此处加载模板，或使用上方按钮上传文件"
              />
            </div>
          )}
        </GlassCard>

        {/* 导入按钮区 */}
        <GlassCard>
          <div className="flex flex-wrap items-center gap-3">
            <AnimeButton
              onClick={handleImportSingle}
              disabled={isPending || !yamlText.trim()}
            >
              {importSingle.isPending
                ? "⏳ 导入中..."
                : "✨ 导入单个角色"}
            </AnimeButton>
            <AnimeButton
              onClick={handleImportBatch}
              disabled={isPending || !yamlText.trim()}
              variant="secondary"
            >
              {importBatch.isPending
                ? "⏳ 批量导入中..."
                : "📚 批量导入"}
            </AnimeButton>
            <span className="text-xs text-twilight-400 ml-2">
              导入后将自动刷新角色列表
            </span>
          </div>
        </GlassCard>

        {/* 导入结果展示 */}
        {result && (
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
          >
            <GlassCard>
              <div
                className={`flex items-start gap-3 ${
                  result.type === "success" ? "text-emerald-600" : "text-red-600"
                }`}
              >
                {result.type === "success" ? (
                  <CheckCircle2 className="w-5 h-5 mt-0.5 shrink-0" />
                ) : (
                  <XCircle className="w-5 h-5 mt-0.5 shrink-0" />
                )}
                <div>
                  <div className="font-semibold">
                    {result.type === "success" ? "导入成功" : "导入失败"}
                  </div>
                  <div className="text-sm mt-1 break-all opacity-80">
                    {result.message}
                  </div>
                </div>
              </div>
            </GlassCard>
          </motion.div>
        )}

        {/* 使用说明 */}
        <GlassCard>
          <h3 className="font-semibold text-twilight-500 mb-3 flex items-center gap-2">
            <span>💡</span>
            使用说明
          </h3>
          <ul className="space-y-2 text-sm text-twilight-400">
            <li className="flex items-start gap-2">
              <span className="text-sakura-400 mt-0.5">•</span>
              <span>
                <strong className="text-twilight-500">单个导入</strong>：YAML
                中包含单个角色定义，使用 <code className="px-1 rounded bg-sakura-50 text-sakura-600">importCharacter</code> 接口
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-sakura-400 mt-0.5">•</span>
              <span>
                <strong className="text-twilight-500">批量导入</strong>：YAML
                中包含多个角色定义（用 <code className="px-1 rounded bg-sakura-50 text-sakura-600">---</code> 分隔），使用 <code className="px-1 rounded bg-sakura-50 text-sakura-600">importCharacterBatch</code> 接口
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-sakura-400 mt-0.5">•</span>
              <span>支持上传 <code className="px-1 rounded bg-sakura-50 text-sakura-600">.yaml</code> / <code className="px-1 rounded bg-sakura-50 text-sakura-600">.yml</code> 文件，也可直接粘贴文本</span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-sakura-400 mt-0.5">•</span>
              <span>导入成功后会自动刷新角色列表，可在「角色」页面查看</span>
            </li>
          </ul>
        </GlassCard>
      </div>
    </NavLayout>
  );
}
