/**
 * auth.js — sign in, create an account, sign out. Loads on login.html.
 *
 * The account is a way to recover an identity, not a session: signing in
 * replaces this browser's device id with the one belonging to the account
 * (`Identity.signIn`), and signing out throws it away for a fresh anonymous
 * one (`Identity.signOut`). There is no token to refresh and nothing to expire.
 */
(() => {
  const signedInPanel = document.getElementById("signedInPanel");
  const signedOutPanel = document.getElementById("signedOutPanel");
  const currentUsername = document.getElementById("currentUsername");

  const loginForm = document.getElementById("loginForm");
  const registerForm = document.getElementById("registerForm");
  const signOutBtn = document.getElementById("signOutBtn");

  const loginMsg = document.getElementById("loginMsg");
  const registerMsg = document.getElementById("registerMsg");
  const signedInMsg = document.getElementById("signedInMsg");

  /** Show a message in one of the form message boxes. */
  function say(el, text, kind) {
    el.textContent = text;
    el.className = `form-msg ${kind}`;
  }

  /** Hide a message box. */
  function clear(el) {
    el.className = "form-msg hidden";
  }

  /** Render whichever panel matches the current account state. */
  function render(username) {
    const signedIn = Boolean(username);
    signedInPanel.classList.toggle("hidden", !signedIn);
    signedOutPanel.classList.toggle("hidden", signedIn);
    if (signedIn) currentUsername.textContent = username;

    const navAccount = document.getElementById("navAccount");
    if (navAccount) navAccount.textContent = signedIn ? username : "Sign in";
  }

  /**
   * Decide what to show. The locally cached username paints the page
   * immediately; the server is then asked for the truth, because the cache can
   * be stale in ways that matter — the account may have been erased from
   * another device, or this browser may hold an identity that never had one.
   */
  async function init() {
    render(Identity.getUsername());
    try {
      const status = await API.authStatus();
      render(status.signed_in ? status.username : null);
      if (!status.signed_in && Identity.getUsername()) {
        // The cache claimed an account the server does not recognise. Drop the
        // label only -- the identity itself is still this participant's.
        Identity.forgetUsername();
        render(null);
      }
    } catch (err) {
      // Backend unreachable: leave the cached view in place rather than
      // pretending the participant has been signed out.
    }
  }

  loginForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    clear(loginMsg);
    const username = document.getElementById("loginUsername").value.trim();
    const password = document.getElementById("loginPassword").value;

    try {
      const result = await API.login(username, password);
      Identity.signIn(result.device_id, result.user.username);
      say(loginMsg, "Signed in. Your profile is on this device now.", "success");
      loginForm.reset();
      render(result.user.username);
    } catch (err) {
      say(loginMsg, err.message, "error");
    }
  });

  registerForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    clear(registerMsg);
    const username = document.getElementById("registerUsername").value.trim();
    const password = document.getElementById("registerPassword").value;
    const repeat = document.getElementById("registerPassword2").value;

    if (password !== repeat) {
      say(registerMsg, "The two passwords do not match.", "error");
      return;
    }

    try {
      const result = await API.register(username, password);
      // No login round trip: registration attaches the account to the identity
      // this browser already has, so it is already signed in.
      Identity.signIn(Identity.getDeviceId(), result.user.username);
      say(registerMsg, "Account created. You are signed in.", "success");
      registerForm.reset();
      render(result.user.username);
    } catch (err) {
      say(registerMsg, err.message, "error");
    }
  });

  signOutBtn.addEventListener("click", () => {
    Identity.signOut();
    render(null);
    say(signedInMsg, "Signed out. This browser is anonymous again.", "success");
    setTimeout(() => clear(signedInMsg), 4000);
  });

  init();
})();
