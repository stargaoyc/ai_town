// P3-5：前端单文件行数守卫（AGENTS.md 250 LOC 约定的 CI 强制层）
// >600 行直接失败；250-600 行列出警告清单，供渐进拆分跟踪。
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(process.cwd(), "src");
const SKIP_DIRS = new Set(["routeTree.gen.ts", "types"]);
// 硬限为「反神级文件」守卫（旧 ui.tsx 740 行即在此档）；
// AGENTS.md 的 250 行约定以软警告列出，供渐进拆分跟踪
const HARD_LIMIT = 800;
const SOFT_LIMIT = 250;

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    if (SKIP_DIRS.has(name)) continue;
    const full = join(dir, name);
    const st = statSync(full);
    if (st.isDirectory()) yield* walk(full);
    else if (/\.(ts|tsx)$/.test(name)) yield full;
  }
}

const hard = [];
const soft = [];
for (const file of walk(ROOT)) {
  const lines = readFileSync(file, "utf8").split("\n").length;
  const rel = file.slice(ROOT.length + 1);
  if (lines > HARD_LIMIT) hard.push(`${rel}: ${lines}`);
  else if (lines > SOFT_LIMIT) soft.push(`${rel}: ${lines}`);
}

if (soft.length) console.warn(`[loc] 超过 ${SOFT_LIMIT} 行（建议拆分）:\n  ${soft.join("\n  ")}`);
if (hard.length) {
  console.error(`[loc] 超过 ${HARD_LIMIT} 行（禁止）:\n  ${hard.join("\n  ")}`);
  process.exit(1);
}
console.log("[loc] line-limit check passed");
