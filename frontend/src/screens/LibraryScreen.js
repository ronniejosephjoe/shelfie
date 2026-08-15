import { useCallback, useEffect, useState } from "react";
import { FlatList, RefreshControl, SafeAreaView, StyleSheet, Text, TouchableOpacity, View } from "react-native";

import { fetchLibrary } from "../api";

export default function LibraryScreen({ onScanAnother }) {
  const [books, setBooks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      setBooks(await fetchLibrary());
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <SafeAreaView style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.heading}>My Library</Text>
        <Text style={styles.subheading}>
          {books.length} book{books.length === 1 ? "" : "s"}
        </Text>
      </View>

      {!!error && (
        <View style={styles.errorBox}>
          <Text style={styles.errorText}>{error}</Text>
        </View>
      )}

      {!loading && books.length === 0 && !error && (
        <View style={styles.centered}>
          <Text style={styles.emptyText}>No books yet. Scan a shelf to get started.</Text>
        </View>
      )}

      <FlatList
        data={books}
        keyExtractor={(b) => b.id}
        contentContainerStyle={styles.list}
        refreshControl={<RefreshControl refreshing={loading} onRefresh={load} />}
        renderItem={({ item }) => (
          <View style={styles.row}>
            <View style={{ flex: 1 }}>
              <Text style={styles.title}>{item.title}</Text>
              <Text style={styles.author}>{item.author}</Text>
            </View>
            {item.was_auto_added && (
              <View style={styles.autoBadge}>
                <Text style={styles.autoBadgeText}>auto</Text>
              </View>
            )}
          </View>
        )}
      />

      <View style={styles.footer}>
        <TouchableOpacity style={styles.primaryButton} onPress={onScanAnother}>
          <Text style={styles.primaryButtonText}>Scan Another Shelf</Text>
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
  list: { paddingHorizontal: 20 },
  row: {
    flexDirection: "row", alignItems: "center", backgroundColor: "#fff", borderRadius: 10,
    padding: 14, marginBottom: 10, borderWidth: 1, borderColor: "#E5E7EB",
  },
  title: { fontSize: 15, fontWeight: "700", color: "#111827" },
  author: { fontSize: 13, color: "#6B7280", marginTop: 2 },
  autoBadge: { backgroundColor: "#DCFCE7", borderRadius: 999, paddingHorizontal: 8, paddingVertical: 3 },
  autoBadgeText: { fontSize: 11, color: "#166534", fontWeight: "700" },
  centered: { padding: 32, alignItems: "center" },
  emptyText: { color: "#9CA3AF", textAlign: "center" },
  errorBox: { backgroundColor: "#FEE2E2", borderRadius: 10, padding: 12, marginHorizontal: 20, marginBottom: 12 },
  errorText: { color: "#991B1B", fontSize: 13 },
  footer: { paddingHorizontal: 20, paddingVertical: 14, borderTopWidth: 1, borderTopColor: "#E5E7EB" },
  primaryButton: { backgroundColor: "#111827", paddingVertical: 14, borderRadius: 10, alignItems: "center" },
  primaryButtonText: { color: "#fff", fontWeight: "700" },
});
