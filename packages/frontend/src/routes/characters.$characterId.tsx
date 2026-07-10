import { createFileRoute, Link } from '@tanstack/react-router';
import { useState } from 'react';
import { GlassCard, LoadingSpinner, ErrorDisplay, ProgressBar, StatCard, EmptyState } from '@/components/ui';
import { useCharacter, useMemories, useMessages, useSendMessage } from '@/lib/queries';

export const Route = createFileRoute('/characters/$characterId')({
  component: CharacterDetailPage,
});

function CharacterDetailPage() {
  const { characterId } = Route.useParams();
  const { data: character, isLoading, error } = useCharacter(characterId);
  const { data: memoriesData } = useMemories(characterId);
  const { data: messagesData } = useMessages(characterId);
  const sendMessage = useSendMessage();
  const [input, setInput] = useState('');

  const handleSend = () => {
    if (!input.trim()) return;
    sendMessage.mutate({ characterId, userId: 'web_user', content: input });
    setInput('');
  };

  if (isLoading) return <LoadingSpinner />;
  if (error) return <ErrorDisplay error={error} />;
  if (!character) return <EmptyState title="角色不存在" />;

  const state = character.state;

  return (
    <div className="space-y-6 animate-fade-in-up">
      <Link to="/characters" className="text-sm text-twilight-400 hover:text-sakura-600 transition-colors">← 返回列表</Link>

      <GlassCard>
        <div className="flex items-center gap-4">
          <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-sakura-300 via-sakura-400 to-twilight-300 flex items-center justify-center text-white font-bold text-3xl shadow-lg">
            {character.name[0]}
          </div>
          <div className="flex-1">
            <h1 className="text-2xl font-bold text-sakura-600">{character.name}</h1>
            <p className="text-twilight-400 mt-1">
              {character.age ? `${character.age}岁 · ` : ''}{character.occupation ?? '未知职业'}
            </p>
            {character.backstory && (
              <p className="text-sm text-twilight-300 mt-2">{character.backstory}</p>
            )}
          </div>
        </div>
      </GlassCard>

      {state && (
        <GlassCard>
          <h3 className="font-semibold text-sakura-600 mb-4 flex items-center gap-2">
            <span>📊</span> 实时状态
          </h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            <StatCard title="位置" value={state.location ?? '未知'} icon="📍" />
            <StatCard title="情绪" value={state.mood ?? 'calm'} icon="😊" color="twilight" />
            <StatCard title="金钱" value={`¥${state.money}`} icon="💰" color="sky" />
            <StatCard title="版本" value={`v${state.version}`} icon="🔄" />
          </div>
          <div className="space-y-4">
            <div>
              <div className="flex justify-between text-sm mb-1.5">
                <span className="text-twilight-400">⚡ 体力</span>
                <span className="text-sakura-600 font-medium">{state.stamina}/100</span>
              </div>
              <ProgressBar value={state.stamina} color="sakura" />
            </div>
            <div>
              <div className="flex justify-between text-sm mb-1.5">
                <span className="text-twilight-400">🍽️ 饱腹度</span>
                <span className="text-sky-soft-500 font-medium">{state.satiety}/100</span>
              </div>
              <ProgressBar value={state.satiety} color="sky" />
            </div>
            <div>
              <div className="flex justify-between text-sm mb-1.5">
                <span className="text-twilight-400">💬 社交能量</span>
                <span className="text-twilight-500 font-medium">{state.social_energy}/100</span>
              </div>
              <ProgressBar value={state.social_energy} color="twilight" />
            </div>
            <div>
              <div className="flex justify-between text-sm mb-1.5">
                <span className="text-twilight-400">📱 手机电量</span>
                <span className="text-sakura-600 font-medium">{state.phone_battery}%</span>
              </div>
              <ProgressBar value={state.phone_battery} color="sakura" />
            </div>
          </div>
        </GlassCard>
      )}

      <GlassCard>
        <h3 className="font-semibold text-sakura-600 mb-4 flex items-center gap-2">
          <span>💬</span> 对话
        </h3>
        <div className="space-y-2 mb-4 max-h-64 overflow-y-auto pr-2">
          {messagesData?.data?.length === 0 && <EmptyState icon="💌" title="暂无消息" subtitle="发送第一条消息开始对话吧" />}
          {messagesData?.data?.map((msg) => (
            <div
              key={msg.id}
              className={`p-2.5 rounded-xl text-sm animate-fade-in-up ${
                msg.sender === 'user'
                  ? 'bg-gradient-to-r from-sakura-100 to-sakura-200/50 text-sakura-700 ml-8 rounded-tr-sm'
                  : msg.sender === 'character'
                  ? 'bg-gradient-to-r from-sky-soft-100 to-sky-soft-200/50 text-sky-soft-500 mr-8 rounded-tl-sm'
                  : 'bg-gray-100/80 text-gray-500'
              }`}
            >
              {msg.content}
            </div>
          )) ?? <p className="text-sm text-twilight-400">加载中...</p>}
        </div>
        <div className="flex gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSend()}
            placeholder="输入消息..."
            className="flex-1 px-4 py-2.5 rounded-xl bg-white/50 border border-sakura-200/50 text-twilight-600 placeholder:text-twilight-300 text-sm focus:outline-none focus:ring-2 focus:ring-sakura-400/50 focus:border-transparent transition-all"
          />
          <button
            onClick={handleSend}
            disabled={sendMessage.isPending || !input.trim()}
            className="px-5 py-2.5 rounded-xl bg-gradient-to-r from-sakura-400 to-sakura-500 text-white text-sm font-medium hover:from-sakura-500 hover:to-sakura-600 disabled:opacity-50 disabled:cursor-not-allowed transition-all hover:scale-105 active:scale-95 shadow-lg shadow-sakura-400/30"
          >
            {sendMessage.isPending ? '...' : '发送'}
          </button>
        </div>
      </GlassCard>

      <GlassCard>
        <h3 className="font-semibold text-sakura-600 mb-4 flex items-center gap-2">
          <span>🧠</span> 最近记忆
        </h3>
        <div className="space-y-3">
          {memoriesData?.data?.length === 0 && <EmptyState icon="💭" title="暂无记忆" />}
          {memoriesData?.data?.map((mem) => (
            <div key={mem.id} className="p-3 rounded-xl bg-white/30 border border-white/20 hover:bg-white/40 transition-colors">
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs text-twilight-400">{new Date(mem.timestamp).toLocaleString('zh-CN')}</span>
                <span className="text-xs text-sakura-400 font-medium">⭐ {mem.importance}</span>
              </div>
              <p className="text-sm text-twilight-500">{mem.content}</p>
            </div>
          )) ?? <p className="text-sm text-twilight-400">加载中...</p>}
        </div>
      </GlassCard>
    </div>
  );
}
