import { API_BASE_URL } from "./config";

/**
 * Thin fetch wrappers. No retry/caching layer on top -- out of scope
 * for an 8-hour exercise (see README "what we cut"). Every function
 * here either resolves with parsed JSON or throws an Error with a
 * message good enough to show a user directly, so screens don't need
 * their own error-shape guessing.
 */

async function handle(response) {
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      // response wasn't JSON -- fall back to the generic message above
    }
    throw new Error(detail);
  }
  return response.json();
}

export async function uploadScan(photo) {
  // photo: { uri, fileName?, mimeType? } from expo-image-picker
  //
  // Every platform goes through the same fetch-to-Blob path now. This
  // used to branch: web fetched the uri into a real Blob (browsers have
  // no polyfill for React Native's classic FormData.append(name, {
  // uri, name, type }) object shape), while native used that classic
  // object shape directly, on the assumption it still worked there.
  // It doesn't, at least not on this RN/Expo Go version -- confirmed by
  // actually running this on a real iOS Simulator, not guessed: it
  // failed with "Unsupported FormDataPart implementation" on the very
  // first real device-class test, meaning this path had never actually
  // been exercised end to end before. `fetch(uri).then(r => r.blob())`
  // works identically on both web and native RN's fetch implementation
  // for a local file:// or blob:/data: uri, so there's no longer a
  // reason to have two code paths -- one working implementation beats
  // one tested path and one that was silently never run.
  const form = new FormData();
  const blobResponse = await fetch(photo.uri);
  const blob = await blobResponse.blob();
  form.append("image", blob, photo.fileName || "shelf.jpg");

  let response;
  try {
    response = await fetch(`${API_BASE_URL}/api/scans/`, { method: "POST", body: form });
  } catch (networkError) {
    throw new Error(
      `Couldn't reach the backend at ${API_BASE_URL}. Is it running, and is API_BASE_URL ` +
        `set correctly for this device? (${networkError.message})`
    );
  }
  return handle(response);
}

export async function fetchScan(scanId) {
  const response = await fetch(`${API_BASE_URL}/api/scans/${scanId}/`);
  return handle(response);
}

export async function decideDetectedBook(detectedBookId, decision) {
  // decision: { action: "confirm" | "correct" | "discard", title?, author? }
  const response = await fetch(`${API_BASE_URL}/api/detected-books/${detectedBookId}/decide/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(decision),
  });
  return handle(response);
}

export async function fetchLibrary() {
  const response = await fetch(`${API_BASE_URL}/api/library/`);
  return handle(response);
}
