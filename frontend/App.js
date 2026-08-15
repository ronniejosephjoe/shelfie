import { useState } from "react";
import { StatusBar } from "expo-status-bar";

import CaptureScreen from "./src/screens/CaptureScreen";
import ReviewScreen from "./src/screens/ReviewScreen";
import LibraryScreen from "./src/screens/LibraryScreen";

/**
 * Three screens, one piece of state (which screen + the current scan
 * result), no router library. React Navigation is the "normal" answer
 * here, but it pulls in native deps (react-native-screens, gesture-
 * handler, safe-area-context) I have no way to verify on a device or
 * simulator in the environment this was built in -- a hand-rolled
 * switch is a few fewer moving parts to be wrong about sight-unseen.
 * Swapping to React Navigation later is a contained change (these
 * three components barely reference navigation at all, just two
 * callback props each) if a real nav stack is worth it later --
 * see README "what's unfinished."
 */
export default function App() {
  const [screen, setScreen] = useState("capture");
  const [currentScan, setCurrentScan] = useState(null);

  return (
    <>
      {screen === "capture" && (
        <CaptureScreen
          onScanComplete={(scan) => {
            setCurrentScan(scan);
            setScreen("review");
          }}
        />
      )}
      {screen === "review" && currentScan && (
        <ReviewScreen
          scan={currentScan}
          onRetake={() => {
            setCurrentScan(null);
            setScreen("capture");
          }}
          onDone={() => setScreen("library")}
        />
      )}
      {screen === "library" && (
        <LibraryScreen
          onScanAnother={() => {
            setCurrentScan(null);
            setScreen("capture");
          }}
        />
      )}
      <StatusBar style="auto" />
    </>
  );
}
