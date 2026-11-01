import {
  LayoutDashboard,
  Camera,
  ShieldAlert,
  Bell,
  BarChart3,
  Settings,
  Radar,
  Video,
  ScanFace,
} from "lucide-react";

const menu = [
  { name: "Dashboard", icon: LayoutDashboard },
  { name: "Live Monitoring", icon: Camera },
  { name: "Violations", icon: ShieldAlert },
  { name: "Alerts", icon: Bell },
  { name: "Footage", icon: Video },
  { name: "Entry Gate", icon: ScanFace },
  { name: "Reports & Analytics", icon: BarChart3 },
  { name: "Settings", icon: Settings },
];

// A controlled nav - which page is active lives in Dashboard.jsx
// (activePage/onNavigate), not here, so Dashboard's own render
// decides what each page actually shows without Sidebar needing to
// know anything about page content.
export default function Sidebar({ activePage, onNavigate }) {
  return (
    <aside className="w-64 min-h-screen bg-zinc-900 border-r border-zinc-800 p-5 relative">
      <div className="flex items-center gap-3 mb-10">
        <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-teal-500 to-amber-500 flex items-center justify-center">
          <Radar size={20} className="text-zinc-950" strokeWidth={2.5} />
        </div>

        <div>
          <h1 className="font-display font-bold text-white tracking-tight">
            Aegis SiteWatch
          </h1>
          <p className="text-xs text-zinc-400">Site Vigilance AI</p>
        </div>
      </div>

      <nav className="space-y-2">
        {menu.map((item) => {
          const Icon = item.icon;
          const active = item.name === activePage;

          return (
            <button
              key={item.name}
              onClick={() => onNavigate(item.name)}
              className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl text-sm transition
                ${
                  active
                    ? "bg-teal-600 text-white"
                    : "text-zinc-400 hover:bg-zinc-800 hover:text-white"
                }`}
            >
              <Icon size={19} />
              {item.name}
            </button>
          );
        })}
      </nav>

      <div className="absolute bottom-5 text-xs text-zinc-500">
        Aegis Vigilance Platform v1.0
      </div>
    </aside>
  );
}
