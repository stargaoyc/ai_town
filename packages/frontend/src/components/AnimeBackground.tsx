import { motion } from "framer-motion";

export function AnimeBackground() {
  return (
    <div className="fixed inset-0 overflow-hidden -z-10 pointer-events-none">
      {/* 渐变背景 */}
      <div className="absolute inset-0 bg-gradient-to-br from-sakura-50 via-sky-50 to-twilight-50" />

      {/* 动态光晕 */}
      <motion.div
        className="absolute top-[-20%] left-[-10%] w-[70vw] h-[70vw] rounded-full bg-sakura-200/30 blur-[120px]"
        animate={{ x: [0, 50, 0], y: [0, 30, 0] }}
        transition={{ duration: 20, repeat: Infinity, ease: "easeInOut" }}
      />
      <motion.div
        className="absolute bottom-[-20%] right-[-10%] w-[60vw] h-[60vw] rounded-full bg-sky-soft-200/30 blur-[120px]"
        animate={{ x: [0, -40, 0], y: [0, -40, 0] }}
        transition={{ duration: 25, repeat: Infinity, ease: "easeInOut" }}
      />
      <motion.div
        className="absolute top-[40%] left-[30%] w-[40vw] h-[40vw] rounded-full bg-twilight-200/20 blur-[100px]"
        animate={{ scale: [1, 1.1, 1] }}
        transition={{ duration: 15, repeat: Infinity, ease: "easeInOut" }}
      />

      {/* 漂浮花瓣 */}
      {[...Array(12)].map((_, i) => (
        <motion.div
          key={i}
          className="absolute w-3 h-3 rounded-full bg-gradient-to-br from-sakura-300 to-sakura-400 opacity-60"
          style={{
            left: `${Math.random() * 100}%`,
            top: "-20px",
            borderRadius: "50% 0 50% 50%",
          }}
          animate={{
            y: ["0vh", "110vh"],
            x: [0, Math.random() * 100 - 50, Math.random() * 100 - 50, 0],
            rotate: [0, 360, 720],
          }}
          transition={{
            duration: 15 + Math.random() * 10,
            repeat: Infinity,
            delay: Math.random() * 10,
            ease: "linear",
          }}
        />
      ))}

      {/* 网格点阵 */}
      <div
        className="absolute inset-0 opacity-[0.03]"
        style={{
          backgroundImage:
            "radial-gradient(circle, #ff8fab 1px, transparent 1px)",
          backgroundSize: "40px 40px",
        }}
      />
    </div>
  );
}
