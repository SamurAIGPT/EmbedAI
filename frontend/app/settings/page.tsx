import { AuthGate } from "@/components/AuthGate";
import { SettingsApp } from "@/components/SettingsApp";

export const metadata = { title: "Settings · Open Dots" };

export default function SettingsPage() {
  return (
    <AuthGate>
      <SettingsApp />
    </AuthGate>
  );
}
