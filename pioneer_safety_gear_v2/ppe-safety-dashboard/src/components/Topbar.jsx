import { useEffect, useState } from "react";
import { LogOut } from "lucide-react";

export default function Topbar({ onLogout }) {
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const interval = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(interval);
  }, []);

  const timeLabel = now.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });

  return (
    <header className="h-20 border-b border-zinc-800 bg-zinc-950 flex items-center justify-between px-8">
      <div>
        <h2 className="text-xl font-display font-semibold text-white tracking-tight">
          Vigilance Console
        </h2>
        <p className="text-sm text-zinc-500">
          Live construction-site safety intelligence
        </p>
      </div>

      <div className="flex items-center gap-6">
        <div className="flex items-center gap-2 text-sm">
          <span className="w-2.5 h-2.5 rounded-full bg-green-500"></span>
          <span className="text-amber-400 font-display font-medium">
            Aegis SiteWatch
          </span>
        </div>

        <div className="text-sm text-zinc-400">{timeLabel}</div>

        {onLogout && (
          <button
            onClick={onLogout}
            title="Log out"
            className="flex items-center gap-1.5 text-xs text-zinc-400 hover:text-red-400 border border-zinc-800 hover:border-red-900 rounded-lg px-3 py-1.5 transition-colors"
          >
            <LogOut size={13} />
            Log out
          </button>
        )}
      </div>
    </header>
  );
}
