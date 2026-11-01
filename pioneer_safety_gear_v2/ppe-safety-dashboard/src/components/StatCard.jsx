export default function StatCard({ title, value, subtitle, icon }) {
  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-5">
      
      <div className="flex justify-between items-start">
        <div>
          <p className="text-sm text-zinc-400">{title}</p>

          <h2 className="text-3xl font-display font-bold text-white mt-2 tracking-tight">
            {value}
          </h2>

          <p className="text-xs text-zinc-500 mt-2">
            {subtitle}
          </p>
        </div>

        <div className="text-2xl text-teal-400">
          {icon}
        </div>
      </div>

    </div>
  );
}
