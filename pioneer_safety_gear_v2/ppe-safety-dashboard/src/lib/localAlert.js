// Fully offline, in-browser alerting: a synthesized tone via the
// Web Audio API (no audio file, no network) and the OS-level
// Notification API (also local, no internet). Both work whether
// or not the backend's optional Twilio SMS is configured.

let audioCtx = null;

function getAudioContext() {
  if (typeof window === "undefined") return null;

  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) return null;

  if (!audioCtx) {
    try {
      audioCtx = new Ctx();
    } catch {
      return null;
    }
  }

  return audioCtx;
}

// Two short beeps - a generic "attention" alert tone synthesized
// entirely client-side, so it works with zero internet access.
export function playAlertTone() {
  const ctx = getAudioContext();
  if (!ctx) return;

  try {
    if (ctx.state === "suspended") {
      ctx.resume();
    }

    const now = ctx.currentTime;

    [0, 0.22].forEach((offset) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();

      osc.type = "square";
      osc.frequency.setValueAtTime(880, now + offset);

      gain.gain.setValueAtTime(0, now + offset);
      gain.gain.linearRampToValueAtTime(0.15, now + offset + 0.02);
      gain.gain.linearRampToValueAtTime(0, now + offset + 0.18);

      osc.connect(gain);
      gain.connect(ctx.destination);

      osc.start(now + offset);
      osc.stop(now + offset + 0.2);
    });
  } catch {
    // Autoplay can be blocked before the user has interacted with
    // the page at all - non-fatal, the visual alert still shows.
  }
}

export function requestNotificationPermission() {
  if (typeof window === "undefined" || !("Notification" in window)) return;

  if (Notification.permission === "default") {
    Notification.requestPermission().catch(() => {});
  }
}

export function notifyIncident(incident) {
  if (typeof window === "undefined" || !("Notification" in window)) return;
  if (Notification.permission !== "granted") return;

  try {
    const title =
      incident.type === "FIRE"
        ? "🔥 Fire detected"
        : incident.type === "SMOKE"
          ? "💨 Smoke detected"
          : `⚠️ ${incident.violation ?? "PPE violation"}`;

    new Notification(title, {
      body: `${incident.camera} · ${incident.location} · ${incident.severity}`,
      tag: incident.id,
    });
  } catch {
    // Some browsers/contexts refuse to construct Notification
    // directly (e.g. requiring a service worker) - never fatal.
  }
}
