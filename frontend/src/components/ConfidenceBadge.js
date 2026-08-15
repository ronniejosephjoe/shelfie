import { StyleSheet, Text, View } from "react-native";

const TIER_STYLE = {
  auto: { label: "Added automatically", bg: "#DCFCE7", fg: "#166534" },
  review: { label: "Needs a look", bg: "#FEF3C7", fg: "#92400E" },
  unmatched: { label: "Not in catalog", bg: "#FEE2E2", fg: "#991B1B" },
};

/**
 * The whole point of this component is that a low-confidence match is
 * never visually indistinguishable from a confident one -- see the
 * brief's "the interface respects the fact that the model is
 * sometimes wrong."
 */
export default function ConfidenceBadge({ tier, score }) {
  const style = TIER_STYLE[tier] || { label: tier || "Unknown", bg: "#E5E7EB", fg: "#374151" };
  return (
    <View style={[styles.badge, { backgroundColor: style.bg }]}>
      <Text style={[styles.text, { color: style.fg }]}>
        {style.label}
        {typeof score === "number" ? `  ${Math.round(score * 100)}%` : ""}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  badge: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999, alignSelf: "flex-start" },
  text: { fontSize: 12, fontWeight: "600" },
});
