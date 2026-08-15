import * as ImagePicker from "expo-image-picker";
import { useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Image,
  SafeAreaView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";

import { uploadScan } from "../api";

/**
 * Step 1 of the flow: take or pick a photo, send it to the backend,
 * hand the full pipeline result to the review screen. All of the
 * "what if this goes wrong" handling lives here at the boundary --
 * permission denial, no photo selected, upload/network failure -- so
 * downstream screens can assume they're working with a valid result.
 */
export default function CaptureScreen({ onScanComplete }) {
  const [photo, setPhoto] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState(null);

  async function pickFrom(source) {
    setError(null);
    const permissionResult =
      source === "camera"
        ? await ImagePicker.requestCameraPermissionsAsync()
        : await ImagePicker.requestMediaLibraryPermissionsAsync();

    if (!permissionResult.granted) {
      Alert.alert(
        "Permission needed",
        source === "camera"
          ? "Shelfie needs camera access to take a photo of your bookshelf."
          : "Shelfie needs photo library access to pick a photo of your bookshelf."
      );
      return;
    }

    const launch = source === "camera" ? ImagePicker.launchCameraAsync : ImagePicker.launchImageLibraryAsync;
    const result = await launch({ mediaTypes: ["images"], quality: 0.85 });
    if (!result.canceled && result.assets?.[0]) {
      setPhoto(result.assets[0]);
    }
  }

  async function scan() {
    if (!photo) return;
    setUploading(true);
    setError(null);
    try {
      const scanResult = await uploadScan(photo);
      onScanComplete(scanResult);
      setPhoto(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setUploading(false);
    }
  }

  return (
    <SafeAreaView style={styles.container}>
      <Text style={styles.heading}>Shelfie</Text>
      <Text style={styles.subheading}>Turn a photo of a bookshelf into your library.</Text>

      <View style={styles.preview}>
        {photo ? (
          <Image source={{ uri: photo.uri }} style={styles.previewImage} resizeMode="cover" />
        ) : (
          <Text style={styles.previewPlaceholder}>No photo selected yet</Text>
        )}
      </View>

      <View style={styles.pickerRow}>
        <TouchableOpacity style={styles.secondaryButton} onPress={() => pickFrom("camera")} disabled={uploading}>
          <Text style={styles.secondaryButtonText}>Take Photo</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.secondaryButton} onPress={() => pickFrom("library")} disabled={uploading}>
          <Text style={styles.secondaryButtonText}>Choose from Library</Text>
        </TouchableOpacity>
      </View>

      {!!error && (
        <View style={styles.errorBox}>
          <Text style={styles.errorText}>{error}</Text>
        </View>
      )}

      <TouchableOpacity
        style={[styles.primaryButton, (!photo || uploading) && styles.primaryButtonDisabled]}
        onPress={scan}
        disabled={!photo || uploading}
      >
        {uploading ? <ActivityIndicator color="#fff" /> : <Text style={styles.primaryButtonText}>Scan This Shelf</Text>}
      </TouchableOpacity>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#F9FAFB", padding: 20 },
  heading: { fontSize: 28, fontWeight: "800", color: "#111827", marginTop: 12 },
  subheading: { fontSize: 14, color: "#6B7280", marginTop: 4, marginBottom: 20 },
  preview: {
    flex: 1, borderRadius: 16, backgroundColor: "#F3F4F6", alignItems: "center",
    justifyContent: "center", overflow: "hidden", marginBottom: 16, borderWidth: 1, borderColor: "#E5E7EB",
  },
  previewImage: { width: "100%", height: "100%" },
  previewPlaceholder: { color: "#9CA3AF" },
  pickerRow: { flexDirection: "row", gap: 10, marginBottom: 12 },
  secondaryButton: { flex: 1, paddingVertical: 12, borderRadius: 10, borderWidth: 1, borderColor: "#D1D5DB", alignItems: "center" },
  secondaryButtonText: { fontWeight: "600", color: "#374151" },
  primaryButton: { backgroundColor: "#111827", paddingVertical: 16, borderRadius: 10, alignItems: "center" },
  primaryButtonDisabled: { backgroundColor: "#9CA3AF" },
  primaryButtonText: { color: "#fff", fontWeight: "700", fontSize: 16 },
  errorBox: { backgroundColor: "#FEE2E2", borderRadius: 10, padding: 12, marginBottom: 12 },
  errorText: { color: "#991B1B", fontSize: 13 },
});
