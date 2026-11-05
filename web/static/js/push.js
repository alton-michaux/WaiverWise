// Keeps the server's copy of this device's push subscription current, and runs the account page's buttons.
(() => {
  const { key, csrf, subscribe, unsubscribe, test } = document.currentScript.dataset;
  const STORED = "waiverwise-push-endpoint";
  const supported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

  const stored = {
    get() { try { return localStorage.getItem(STORED); } catch { return null; } },
    set(value) { try { localStorage.setItem(STORED, value); } catch {} },
    clear() { try { localStorage.removeItem(STORED); } catch {} },
  };
  const post = (url, body) => fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
    body: JSON.stringify(body || {}),
  });
  const report = async (subscription) => {
    const res = await post(subscribe, subscription.toJSON());
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    stored.set(subscription.endpoint);
  };
  const applicationServerKey = () => {
    const text = atob(key.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(key.length / 4) * 4, "="));
    return Uint8Array.from(text, (c) => c.charCodeAt(0));
  };

  if (supported && Notification.permission === "granted") {
    navigator.serviceWorker.ready
      .then((reg) => reg.pushManager.getSubscription())
      .then((sub) => { if (sub && sub.endpoint !== stored.get()) return report(sub); })
      .catch(() => {});  // nice to have; the next page load tries again
  }

  const panel = document.getElementById("push-panel");
  if (!panel) return;
  const part = (name) => panel.querySelector(`[data-push-${name}]`);
  const [status, on, off, tester] = ["status", "on", "off", "test"].map(part);
  const show = (text, { canEnable = false, enabled = false } = {}) => {
    status.textContent = text;
    on.hidden = !canEnable;
    off.hidden = tester.hidden = !enabled;
  };

  if (!supported) {
    const iphone = /iPad|iPhone|iPod/.test(navigator.userAgent) && !navigator.standalone;
    show(iphone
      ? "On iPhone, add WaiverWise to your Home Screen first (Share, then Add to Home Screen), and open it from there."
      : "This browser can't receive notifications.");
    return;
  }

  const refresh = async () => {
    if (Notification.permission === "denied") {
      show("Notifications are blocked for this site. Allow them in your browser or phone settings, then reload.");
      return;
    }
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub) show("On for this device.", { enabled: true });
    else show("Off for this device.", { canEnable: true });
  };

  on.addEventListener("click", async () => {
    on.setAttribute("aria-busy", "true");
    try {
      if (await Notification.requestPermission() !== "granted") return await refresh();
      const reg = await navigator.serviceWorker.ready;
      await report(await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: applicationServerKey() }));
      await refresh();
    } catch {
      show("Couldn't turn them on. Try again in a minute.", { canEnable: true });
    } finally {
      on.removeAttribute("aria-busy");
    }
  });

  off.addEventListener("click", async () => {
    off.setAttribute("aria-busy", "true");
    try {
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.getSubscription();
      if (sub) {
        await post(unsubscribe, { endpoint: sub.endpoint });
        await sub.unsubscribe();
      }
      stored.clear();
      await refresh();
    } catch {
      show("Couldn't turn them off. Try again in a minute.", { enabled: true });
    } finally {
      off.removeAttribute("aria-busy");
    }
  });

  tester.addEventListener("click", async () => {
    tester.setAttribute("aria-busy", "true");
    try {
      const res = await post(test);
      const { sent } = await res.json();
      status.textContent = sent ? "Sent. It should show up in a few seconds." : "That didn't go through. Try turning this off and on again.";
    } catch {
      status.textContent = "Couldn't reach the server. Try again in a minute.";
    } finally {
      tester.removeAttribute("aria-busy");
    }
  });

  refresh();
})();
