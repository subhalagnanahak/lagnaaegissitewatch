import { Flame, MapPin } from "lucide-react";

/*
 * Ranks zones by how many alerts have actually fired there this
 * session (GET /analytics/hotspots - the same cooldown-gated alerts
 * the Alert Log lists chronologically, just grouped by zone instead).
 * The site-wide, cross-camera counterpart to each camera's own live
 * pixel-space heatmap overlay (the thermometer toggle in Camera &
 * Detection Settings) - this panel answers "which ZONE needs
 * attention", that overlay answers "WHERE in this camera's frame".
 *
 * Bar length is the real encoding (proportional to that zone's share
 * of the worst zone's count) - the amber intensity by rank is a
 * secondary, purely decorative reinforcement of the same ranking, so
 * nothing here depends on color alone; the count is always printed
 * as a number too.
 *
 * `zones` - the backend's `zones` array, already sorted descending by
 * count: [{ zone, camera_ids, count, ppe_count, fire_smoke_count,
 * by_type }, ...].
 */

// Sequential, single-hue (amber), light -> dark as rank rises. Kept
// distinct from the app's existing status colors (red = fire, orange
// = smoke, yellow/teal = compliance) so it doesn't compete with those
// meanings elsewhere on the same page - amber reads as "attention
// map", not as a specific incident type.
function intensityClass(rank) {
  if (rank === 0) return "bg-amber-500";
  if (rank === 1) return "bg-amber-600/90";
  if (rank === 2) return "bg-amber-700/80";
  return "bg-amber-800/60";
}

export default function ViolationHotspots({ zones }) {
  const hasData = Array.isArray(zones) && zones.length > 0;
  const maxCount = hasData ? zones[0].count : 0;

  return (
    <section>
      <div className="flex items-center gap-2 mb-4">
        <Flame size={19} className="text-amber-400" />
        <div>
          <h2 className="text-lg font-display font-semibold text-white tracking-tight">
            Violation Hotspots
          </h2>
          <p className="text-sm text-zinc-500">
            Zones ranked by alerts fired this session
          </p>
        </div>
      </div>

      <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-5">
        {!hasData ? (
          <div className="p-2 text-center">
            <MapPin className="mx-auto text-zinc-600 mb-2" size={28} />
            <p className="text-sm text-zinc-400">
              No alerts have fired yet this session
            </p>
            <p className="text-xs text-zinc-500 mt-1">
              A zone appears here the moment its first alert fires
            </p>
          </div>
        ) : (
          <div className="space-y-3.5">
            {zones.slice(0, 8).map((z, index) => {
              const widthPercent =
                maxCount > 0 ? Math.max(6, Math.round((z.count / maxCount) * 100)) : 0;

              return (
                <div key={z.zone}>
                  <div className="flex items-center justify-between gap-2 text-xs mb-1">
                    <span className="text-zinc-200 font-medium truncate flex items-center gap-1.5 min-w-0">
                      <MapPin size={11} className="text-zinc-500 shrink-0" />
                      <span className="truncate">{z.zone}</span>
                      <span className="text-zinc-600 font-normal truncate hidden sm:inline">
                        ({z.camera_ids.join(", ")})
                      </span>
                    </span>
                    <span className="text-zinc-300 font-semibold shrink-0">
                      {z.count}
                    </span>
                  </div>

                  <div className="h-2.5 w-full bg-zinc-800 rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full ${intensityClass(index)}`}
                      style={{ width: `${widthPercent}%` }}
                    />
                  </div>

                  {z.fire_smoke_count > 0 && (
                    <p className="text-[10px] text-red-400 mt-1">
                      {z.fire_smoke_count} fire/smoke &middot; {z.ppe_count} PPE
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </section>
  );
}
