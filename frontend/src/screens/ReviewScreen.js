import { useState } from "react";
import { FlatList, SafeAreaView, StyleSheet, Text, TouchableOpacity, View } from "react-native";

import { decideDetectedBook } from "../api";
import DetectedBookCard from "../components/DetectedBookCard";

/**
 * Step 2: show what the pipeline found. This screen has to handle
 * every outcome in the brief's "graceful failure" list without a blank
 * or broken-looking screen:
 *   - the scan itself failed (bad image, backend error)
 *   - zero spines detected (a valid, if disappointing, outcome)
 *   - some spines detected, none matched confidently
 * plus the ordinary case of a mix of auto-added / needs-review books.
 */
export default function ReviewScreen({ scan, onDone, onRetake }) {
  const [books, setBooks] = useState(scan.detected_books);

  async function handleDecide(detectedBookId, decision) {
    try {
      const result = await decideDetectedBook(detectedBookId, decision);
      const updated = result.detected_book || result; // discard returns the book directly
      setBooks((prev) => prev.map((b) => (b.id === detectedBookId ? updated : b)));
    } catch (err) {
      // A failed decide() shouldn't lose the user's place -- the card
      // just stays in its current (pending) state so they can retry.
      console.warn("decide failed:", err.message);
    }
  }

  if (scan.status === "failed") {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.centered}>
          <Text style={styles.failTitle}>This scan didn't work out</Text>
          <Text style={styles.failMessage}>
            {scan.error_message || "Something went wrong processing that photo."}
          </Text>
          <TouchableOpacity style={styles.primaryButton} onPress={onRetake}>
            <Text style={styles.primaryButtonText}>Try Another Photo</Text>
          </TouchableOpacity>
        </View>
      </SafeAreaView>
    );
  }

  if (books.length === 0) {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.centered}>
          <Text style={styles.failTitle}>No book spines found</Text>
          <Text style={styles.failMessage}>
            Try a photo with more even lighting, taken straighter-on to the shelf, with the spines
            large enough to read.
          </Text>
          <TouchableOpacity style={styles.primaryButton} onPress={onRetake}>
            <Text style={styles.primaryButtonText}>Try Another Photo</Text>
          </TouchableOpacity>
        </View>
      </SafeAreaView>
    );
  }

  const pendingCount = books.filter((b) => b.review_status === "pending_review").length;

  return (
    <SafeAreaView style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.heading}>Review</Text>
        <Text style={styles.subheading}>
          {books.length} spine{books.length === 1 ? "" : "s"} found
          {pendingCount > 0 ? ` — ${pendingCount} need a decision` : " — all set"}
        </Text>
        <Text style={styles.stats}>
          local model: {Math.round(scan.local_model_ms || 0)}ms · VLM: {Math.round(scan.vlm_total_ms || 0)}ms
          {scan.estimated_cost_usd ? ` · ~$${scan.estimated_cost_usd.toFixed(4)}` : ""}
        </Text>
      </View>

      <FlatList
        data={books}
        keyExtractor={(b) => b.id}
        contentContainerStyle={styles.list}
        renderItem={({ item }) => <DetectedBookCard book={item} onDecide={handleDecide} />}
      />

      <View style={styles.footer}>
        <TouchableOpacity style={styles.secondaryButton} onPress={onRetake}>
          <Text style={styles.secondaryButtonText}>Scan Another Shelf</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.primaryButton} onPress={onDone}>
          <Text style={styles.primaryButtonText}>View My Library</Text>
        </TouchableOpacity>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#F9FAFB" },
  header: { paddingHorizontal: 20, paddingTop: 16, paddingBottom: 8 },
  heading: { fontSize: 24, fontWeight: "800", color: "#111827" },
  subheading: { fontSize: 13, color: "#6B7280", marginTop: 2 },
  stats: { fontSize: 11, color: "#9CA3AF", marginTop: 4 },
  list: { paddingHorizontal: 20, paddingBottom: 12 },
  footer: { flexDirection: "row", gap: 10, paddingHorizontal: 20, paddingVertical: 14, borderTopWidth: 1, borderTopColor: "#E5E7EB" },
  centered: { flex: 1, alignItems: "center", justifyContent: "center", padding: 32 },
  failTitle: { fontSize: 18, fontWeight: "700", color: "#111827", marginBottom: 8, textAlign: "center" },
  failMessage: { fontSize: 14, color: "#6B7280", textAlign: "center", marginBottom: 20 },
  primaryButton: { flex: 1, backgroundColor: "#111827", paddingVertical: 14, borderRadius: 10, alignItems: "center" },
  primaryButtonText: { color: "#fff", fontWeight: "700" },
  secondaryButton: { flex: 1, paddingVertical: 14, borderRadius: 10, borderWidth: 1, borderColor: "#D1D5DB", alignItems: "center" },
  secondaryButtonText: { color: "#374151", fontWeight: "600" },
});
