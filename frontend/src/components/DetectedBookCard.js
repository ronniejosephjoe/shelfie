import { useState } from "react";
import { ActivityIndicator, Image, StyleSheet, Text, TextInput, TouchableOpacity, View } from "react-native";

import ConfidenceBadge from "./ConfidenceBadge";

const READ_ERROR_MESSAGES = {
  timeout: "The reader timed out on this spine.",
  api_error: "The reader had a problem with this spine.",
  malformed_json: "Got an unusable response reading this spine.",
  unreadable: "Couldn't make out any text on this spine.",
};

/**
 * One detected book, in whatever state it's in. This is the human-in-
 * the-loop surface the brief asks for: a low-confidence or unmatched
 * detection is never auto-accepted and never silently dropped -- it
 * sits here, visibly needing a decision, until the user confirms,
 * corrects, or explicitly discards it.
 */
export default function DetectedBookCard({ book, onDecide }) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(book.match_title || book.read_title || "");
  const [author, setAuthor] = useState(book.match_author || book.read_author || "");
  const [busy, setBusy] = useState(false);

  const isFinal = ["auto_added", "confirmed", "corrected", "discarded"].includes(book.review_status);

  async function act(action, extra = {}) {
    setBusy(true);
    try {
      await onDecide(book.id, { action, ...extra });
    } finally {
      setBusy(false);
    }
  }

  return (
    <View style={styles.card}>
      <View style={styles.row}>
        {book.crop_url ? (
          <Image source={{ uri: book.crop_url }} style={styles.thumb} resizeMode="cover" />
        ) : (
          <View style={[styles.thumb, styles.thumbPlaceholder]} />
        )}

        <View style={styles.info}>
          {book.review_status === "auto_added" && (
            <>
              <Text style={styles.title}>{book.final_title}</Text>
              <Text style={styles.author}>{book.final_author}</Text>
            </>
          )}
          {(book.review_status === "confirmed" || book.review_status === "corrected") && (
            <>
              <Text style={styles.title}>{book.final_title}</Text>
              <Text style={styles.author}>{book.final_author}</Text>
            </>
          )}
          {book.review_status === "discarded" && (
            <Text style={styles.discardedText}>Discarded</Text>
          )}
          {book.review_status === "pending_review" && !editing && (
            <>
              <Text style={styles.title}>
                {book.match_title || book.read_title || "(no title read)"}
              </Text>
              {!!(book.match_author || book.read_author) && (
                <Text style={styles.author}>{book.match_author || book.read_author}</Text>
              )}
              {!!book.read_error && (
                <Text style={styles.errorText}>
                  {READ_ERROR_MESSAGES[book.read_error] || book.read_error}
                </Text>
              )}
            </>
          )}
          {editing && (
            <View style={styles.editForm}>
              <TextInput style={styles.input} value={title} onChangeText={setTitle} placeholder="Title" />
              <TextInput style={styles.input} value={author} onChangeText={setAuthor} placeholder="Author" />
            </View>
          )}

          {!isFinal && !!book.match_tier && (
            <View style={{ marginTop: 6 }}>
              <ConfidenceBadge tier={book.match_tier} score={book.match_score} />
            </View>
          )}
        </View>
      </View>

      {!!book.match_alternates?.length && book.review_status === "pending_review" && !editing && (
        <View style={styles.alternates}>
          <Text style={styles.alternatesLabel}>Did you mean:</Text>
          {book.match_alternates.slice(0, 3).map((alt) => (
            <TouchableOpacity
              key={alt.catalog_id}
              onPress={() => {
                setTitle(alt.title);
                setAuthor(alt.author);
                setEditing(true);
              }}
            >
              <Text style={styles.alternateOption}>
                • {alt.title} — {alt.author} ({Math.round(alt.score * 100)}%)
              </Text>
            </TouchableOpacity>
          ))}
        </View>
      )}

      {!isFinal && (
        <View style={styles.actions}>
          {busy ? (
            <ActivityIndicator />
          ) : editing ? (
            <>
              <TouchableOpacity
                style={[styles.button, styles.primaryButton]}
                onPress={() => act("correct", { title, author })}
              >
                <Text style={styles.primaryButtonText}>Save</Text>
              </TouchableOpacity>
              <TouchableOpacity style={styles.button} onPress={() => setEditing(false)}>
                <Text style={styles.buttonText}>Cancel</Text>
              </TouchableOpacity>
            </>
          ) : (
            <>
              {!!(book.match_title || book.read_title) && (
                <TouchableOpacity style={[styles.button, styles.primaryButton]} onPress={() => act("confirm")}>
                  <Text style={styles.primaryButtonText}>Confirm</Text>
                </TouchableOpacity>
              )}
              <TouchableOpacity style={styles.button} onPress={() => setEditing(true)}>
                <Text style={styles.buttonText}>Correct</Text>
              </TouchableOpacity>
              <TouchableOpacity style={styles.button} onPress={() => act("discard")}>
                <Text style={styles.buttonText}>Discard</Text>
              </TouchableOpacity>
            </>
          )}
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { backgroundColor: "#fff", borderRadius: 12, padding: 12, marginBottom: 12, borderWidth: 1, borderColor: "#E5E7EB" },
  row: { flexDirection: "row" },
  thumb: { width: 56, height: 84, borderRadius: 6, backgroundColor: "#E5E7EB" },
  thumbPlaceholder: { alignItems: "center", justifyContent: "center" },
  info: { flex: 1, marginLeft: 12, justifyContent: "center" },
  title: { fontSize: 15, fontWeight: "700", color: "#111827" },
  author: { fontSize: 13, color: "#4B5563", marginTop: 2 },
  errorText: { fontSize: 12, color: "#B91C1C", marginTop: 4, fontStyle: "italic" },
  discardedText: { fontSize: 14, color: "#9CA3AF", fontStyle: "italic" },
  editForm: { gap: 6 },
  input: { borderWidth: 1, borderColor: "#D1D5DB", borderRadius: 8, paddingHorizontal: 10, paddingVertical: 6, fontSize: 14 },
  alternates: { marginTop: 10, paddingTop: 10, borderTopWidth: 1, borderTopColor: "#F3F4F6" },
  alternatesLabel: { fontSize: 12, color: "#6B7280", marginBottom: 4 },
  alternateOption: { fontSize: 13, color: "#2563EB", paddingVertical: 2 },
  actions: { flexDirection: "row", gap: 8, marginTop: 10 },
  button: { paddingHorizontal: 12, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: "#D1D5DB" },
  buttonText: { fontSize: 13, color: "#374151", fontWeight: "600" },
  primaryButton: { backgroundColor: "#111827", borderColor: "#111827" },
  primaryButtonText: { fontSize: 13, color: "#fff", fontWeight: "600" },
});
