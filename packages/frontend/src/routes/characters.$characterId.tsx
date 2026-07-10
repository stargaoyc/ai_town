import { createFileRoute, Link } from '@tanstack/react-router';
import { useState } from 'react';
import { GlassCard, LoadingSpinner, ErrorDisplay, ProgressBar, StatCard } from '@/components/ui';
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
  if (!character) return null;

  const state = character.state;

  return (
    <div className="space-y-6">
      <Link to="/characters" className="text-sm text-twilight-400 hover:text-sakura-600">← 返回列表</Link>

      <GlassCard>
        <div className="flex items-center gap-4">
          <div className="w-16 h-16 rounded-full bg-sakura-300 flex items-center justify-center text-sakura-600 font-bold text-2xl">
            {character.name[0]}
          </div>
          <div>
            <h2 className="text-2xl font-bold text-sakura-600">{character.name}</h2>
            <p className="text-twilight-400">
              {character.age ? `${character.age}岁 · ` : ''}{character.occupation ?? '未知'}
            </p>
            {character.backstory && (
              <p className="text-sm text-twilight-400 mt-1">{character.backstory}</p>
            )}
          </div>
        </div>
      </GlassCard>

      {state && (
        <GlassCard>
          <h3 className="font-semibold text-sakura-600 mb-4">实时状态</h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
            <StatCard title="位置" value={state.location ?? '未知'} icon="📍" />
            <StatCard title="情绪" value={state.mood ?? 'calm'} icon="😊" color="twilight" />
            <StatCard title="金钱" value={state.money} icon="💰" color="sky" />
            <StatCard title="版本" value={`v${state.version}`} icon="🔄" />
          </div>
          <div className="space-y-3">
            <div>
              <div className="flex justify-between text-sm mb-1">
                <span className="text-twilight-400">体力</span>
                <span className="text-sakura-600">{state.stamina}/100</span>
              </div>
              <ProgressBar value={state.stamina} color="sakura" />
            </div>
            <div>
              <div className="flex justify-between text-sm mb-1">
                <span className="text-twilight-400">饱腹度</span>
                <span className="text-sky-soft-500">{state.satiety}/100</span>
              </div>
              <ProgressBar value={state.satiety} color="sky" />
            </div>
            <div>
              <div className="flex justify-between text-sm mb-1">
                <span className="text-twilight-400">社交能量</span>
                <span className="text-twilight-500">{state.social_energy}/100</span>
              </div>
              <ProgressBar value={state.social_energy} color="twilight" />
            </div>
            <div>
              <div className="flex justify-between text-sm mb-1">
                <span className="text-twilight-400">手机电量</span>
                <span className="text-sakura-600">{state.phone_battery}%</span>
              </div>
              <ProgressBar value={state.phone_battery} color="sakura" />
            </div>
          </div>
        </GlassCard>
      )}

      <GlassCard>
        <h3 className="font-semibold text-sakura-600 mb-4">对话</h3>
        <div className="space-y-2 mb-4 max-h-64 overflow-y-auto">
          {messagesData?.data?.map((msg) => (
            <div
              key={msg.id}
              className={`p-2 rounded-lg text-sm ${msg.sender === 'user' ? 'bg-sakura-100 text-sakura-700 ml-8' : msg.sender === 'character' ? 'bg-sky-soft-100 text-sky-soft-500 mr-8' : 'bg-gray-100 text-gray-500'}`}
            >
              {msg.content}
            </div>
          )) ?? <p className="text-sm text-twilight-400">暂无消息</p>}
        </div>
        <div className="flex gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSend()}
            placeholder="输入消息..."
            className="flex-1 px-3 py-2 rounded-lg border border-sakura-200 bg-white/50 text-sm focus:outline-none focus:border-sakura-400"
          />
          <button
            onClick={handleSend}
            disabled={sendMessage.isPending || !input.trim()}
            className="px-4 py-2 rounded-lg bg-sakura-400 text-white text-sm hover:bg-sakura-500 disabled:opacity-50 transition-colors"
          >
            发送
          </button>
        </div>
      </GlassCard>

      <GlassCard>
        <h3 className="font-semibold text-sakura-600 mb-4">最近记忆</h3>
        <div className="space-y-2">
          {memoriesData?.data?.map((mem) => (
            <div key={mem.id} className="p-3 rounded-lg bg-white/30">
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs text-twilight-400">{new Date(mem.timestamp).toLocaleString()}</span>
                <span className="text-xs text-sakura-400">重要度: {mem.importance}</span>
              </div>
              <p className="text-sm text-twilight-500">{mem.content}</p>
            </div>
          )) ?? <p className="text-sm text-twilight-400">暂无记忆</p>}
        </div>
      </GlassCard>
    </div>
  );
}
