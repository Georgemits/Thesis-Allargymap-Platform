/**
 * identity.js — anonymous device identity for AllergyMap.
 *
 * AllergyMap has no accounts. A participant is a *device*, identified by a
 * random UUID v4 generated here on first visit and kept in `localStorage`.
 * Every identity-bearing request carries it in the `X-Device-Id` header
 * (added centrally by api.js), and the backend validates it before any route
 * runs — see backend/app/utils/identity.py for the full rationale.
 *
 * Because the identifier is the only credential, it is also the participant's
 * *transfer code*: typing it on a second device adopts the same profile. The
 * profile page surfaces it under that name.
 *
 * An optional account (username + password) sits on top of this: signing in
 * hands the browser the identifier belonging to that account, and signing out
 * throws it away and generates a fresh anonymous one. The account is a way to
 * recover an identity, never a second notion of "who" -- see
 * backend/app/routes/auth.py.
 *
 * Load this file before api.js. It must not depend on api.js, so that a page
 * can establish an identity even if the backend is unreachable.
 */
const Identity = (() => {
  const STORAGE_KEY = "allergymap_device_id";

  // The signed-in username, cached locally purely so the navbar can render it
  // without a request on every page load. The server remains the authority --
  // `API.authStatus()` is what the account pages ask.
  const USERNAME_KEY = "allergymap_username";

  // Pre-identity-layer key: report.js used to mint its own UUID here. Reports
  // already in the database are attributed to that value, so it is migrated
  // rather than discarded — otherwise every existing participant would look
  // like a brand-new one the first time they load the new build.
  const LEGACY_STORAGE_KEY = "allergymap_uid";

  const UUID_V4 =
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

  // Used only when localStorage is unavailable (Safari private browsing throws
  // on access). The identity then lasts for the tab, which keeps the app
  // working instead of failing every request.
  let memoryFallback = null;
  let memoryUsername = "";

  /** Trim and lower-case a candidate identifier. */
  function normalize(value) {
    return String(value === null || value === undefined ? "" : value)
      .trim()
      .toLowerCase();
  }

  /** True when `value` is a canonical UUID v4. */
  function isValid(value) {
    return UUID_V4.test(normalize(value));
  }

  /**
   * Generate a UUID v4.
   *
   * `crypto.randomUUID()` is only exposed in a secure context (HTTPS or
   * localhost), so a deployment reached over plain HTTP at an IP address
   * would not have it. `crypto.getRandomValues()` *is* available there, and
   * is the real requirement: the identifier doubles as a bearer credential,
   * so it has to be unpredictable. `Math.random()` is a last resort that
   * keeps the page functional on a browser with neither.
   */
  function generate() {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
      return crypto.randomUUID();
    }
    if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
      const bytes = crypto.getRandomValues(new Uint8Array(16));
      bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
      bytes[8] = (bytes[8] & 0x3f) | 0x80; // RFC 4122 variant
      const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
      return (
        hex.slice(0, 8) + "-" + hex.slice(8, 12) + "-" + hex.slice(12, 16) +
        "-" + hex.slice(16, 20) + "-" + hex.slice(20)
      );
    }
    console.warn(
      "identity.js: no Web Crypto available, falling back to Math.random(). " +
        "The device id will be weaker than intended."
    );
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      const v = c === "x" ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  /** Read a key from localStorage, tolerating a browser that forbids it. */
  function readStored(key) {
    try {
      return localStorage.getItem(key);
    } catch (err) {
      return null;
    }
  }

  /** Write the identity, falling back to memory when storage is forbidden. */
  function writeStored(value) {
    memoryFallback = value;
    try {
      localStorage.setItem(STORAGE_KEY, value);
    } catch (err) {
      console.warn("identity.js: localStorage unavailable, identity is tab-scoped.");
    }
    return value;
  }

  /**
   * Return this device's identifier, creating one on first use.
   *
   * Resolution order: the current key, then the legacy key (migrated in place
   * so existing reports stay attributed), then a freshly generated UUID. A
   * stored value that is not a valid UUID v4 is replaced: the old code had a
   * `Math.random().toString(36)` fallback that produced identifiers the
   * backend now rejects.
   *
   * @returns {string} canonical UUID v4
   */
  function getDeviceId() {
    const stored = normalize(readStored(STORAGE_KEY) || memoryFallback);
    if (isValid(stored)) return stored;

    const legacy = normalize(readStored(LEGACY_STORAGE_KEY));
    if (isValid(legacy)) return writeStored(legacy);

    return writeStored(generate());
  }

  /**
   * Adopt an identity from a transfer code typed on another device.
   *
   * @param {string} code transfer code (a UUID v4)
   * @returns {string} the adopted identifier
   * @throws {Error} when the code is not a valid transfer code
   */
  function adopt(code) {
    const candidate = normalize(code);
    if (!isValid(candidate)) {
      throw new Error("That is not a valid transfer code.");
    }
    return writeStored(candidate);
  }

  /**
   * Abandon this identity and start a fresh one.
   *
   * The old identity is not deleted server-side — that is what
   * `API.eraseMe()` is for. This only stops *this browser* from using it,
   * which is what someone lending their laptop to another participant wants.
   *
   * @returns {string} the new identifier
   */
  function reset() {
    return writeStored(generate());
  }

  /**
   * Adopt the identity of an account after a successful sign-in.
   *
   * @param {string} deviceId identifier returned by `API.login()`
   * @param {string} username for display only
   * @returns {string} the adopted identifier
   * @throws {Error} if the server returned something that is not a valid id
   */
  function signIn(deviceId, username) {
    const adopted = adopt(deviceId);
    memoryUsername = String(username || "");
    try {
      localStorage.setItem(USERNAME_KEY, memoryUsername);
    } catch (err) {
      /* tab-scoped fallback, same as the identifier itself */
    }
    return adopted;
  }

  /**
   * Forget the account and start a fresh anonymous identity.
   *
   * Nothing is deleted server-side: the account and its data stay, and signing
   * in again brings them back. This is only "stop using it on this browser",
   * which is what matters on a shared computer.
   *
   * @returns {string} the new anonymous identifier
   */
  function signOut() {
    memoryUsername = "";
    try {
      localStorage.removeItem(USERNAME_KEY);
    } catch (err) {
      /* nothing stored to remove */
    }
    return reset();
  }

  /**
   * Forget the cached username without touching the identity.
   *
   * For the case where the local cache claims an account the server does not
   * recognise -- erased from another device, or left behind after adopting a
   * transfer code. `signOut` would be wrong here: it also mints a new device
   * id, throwing away the identity (and the data behind it) over what is only
   * a stale label.
   */
  function forgetUsername() {
    memoryUsername = "";
    try {
      localStorage.removeItem(USERNAME_KEY);
    } catch (err) {
      /* nothing stored to remove */
    }
  }

  /** @returns {string|null} the signed-in username, or null when anonymous. */
  function getUsername() {
    let stored = null;
    try {
      stored = localStorage.getItem(USERNAME_KEY);
    } catch (err) {
      stored = memoryUsername;
    }
    const name = (stored || memoryUsername || "").trim();
    return name === "" ? null : name;
  }

  return {
    getDeviceId,
    adopt,
    reset,
    signIn,
    signOut,
    forgetUsername,
    getUsername,
    isValid,
    normalize,
    STORAGE_KEY,
    USERNAME_KEY,
  };
})();
