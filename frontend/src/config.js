/**
 * API_BASE_URL has to point at whatever machine is running the Django
 * backend, reachable from wherever this app is actually running:
 *
 *  - Expo web / iOS simulator on the same machine as the backend:
 *    http://localhost:8000 works.
 *  - Expo Go on a physical phone: needs your computer's LAN IP (e.g.
 *    http://192.168.1.23:8000), not localhost -- the phone is a
 *    separate device on the network. Find it with `ipconfig getifaddr
 *    en0` (macOS) or `hostname -I` (Linux).
 *
 * Set it via an env var so nobody has to edit source to run this:
 *   EXPO_PUBLIC_API_BASE_URL=http://192.168.1.23:8000 npx expo start
 * (EXPO_PUBLIC_ vars are inlined by Expo at build time -- see README.)
 */
export const API_BASE_URL = process.env.EXPO_PUBLIC_API_BASE_URL || "http://localhost:8000";
