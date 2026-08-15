import { Platform } from "react-native";

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
  const form = new FormData();

  if (Platform.OS === "web") {
    // React Native's FormData.append(name, { uri, name, type }) shape is
    // handled by a native polyfill that doesn't exist in a real browser --
    // on web, expo-image-picker's uri is a blob:/data: URL, and the only
    // way to attach it correctly is to fetch that URL back into an actual
    // Blob and append that. Skipping this branch is exactly the kind of
    // bug that works fine on a phone and silently sends an empty request
    // on web (found by actually running this in a browser, not guessed).
    const blobResponse = await fetch(photo.uri);
    const blob = await blobResponse.blob();
    form.append("image", blob, photo.fileName || "shelf.jpg");
  } else {
    form.append("image", {
      uri: photo.uri,
      name: photo.fileName || "shelf.jpg",
      type: photo.mimeType || "image/jpeg",
    });
  }

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
