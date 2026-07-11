// Login / first-run setup. On first run (no password set) the same form sets
// the initial admin password; otherwise it logs in.
async function init() {
  const auth = await authState();
  const isSetup = auth.setup_required;
  setText(document.getElementById("title"), isSetup ? "Set up your portal" : "Login");
  setText(document.getElementById("subtitle"),
    isSetup
      ? "Create the admin password. You'll use it to edit apps and settings."
      : "Enter your password to edit the portal.");

  const field = document.getElementById("pw");
  field.placeholder = isSetup ? "Choose a password" : "Password";
  field.autocomplete = isSetup ? "new-password" : "current-password";

  document.getElementById("form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const pw = field.value;
    const msg = document.getElementById("msg");
    msg.className = "msg";
    setText(msg, "");
    if (!pw) { msg.className = "msg err"; setText(msg, "Password is required."); return; }
    try {
      // Raw fetch — a 401 here must NOT redirect to /login (we're already here).
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ password: pw }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "login_failed");
      location.href = nextPath();
    } catch (err) {
      msg.className = "msg err";
      setText(msg, err.message === "invalid_password" ? "Wrong password." : "Something went wrong.");
    }
  });
}

init();