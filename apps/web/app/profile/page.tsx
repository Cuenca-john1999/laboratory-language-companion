import type { Profile } from "@deutschos/shared";
import { ProfileForm } from "../../components/ProfileForm";
import { getProfile } from "../../lib/api";

async function loadProfile(): Promise<Profile | null> {
  try {
    return await getProfile();
  } catch {
    return null;
  }
}

export default async function ProfilePage() {
  const profile = await loadProfile();
  if (!profile) {
    return (
      <section className="empty">
        <h1>No se pudo cargar el perfil.</h1>
        <p>Inicia la API y aplica las migraciones.</p>
      </section>
    );
  }
  return (
    <section>
      <p className="eyebrow">TU CONTEXTO</p>
      <h1>Perfil de aprendizaje</h1>
      <p className="lead">
        Esto es memoria estructurada, editable y local. El profesor no depende
        del historial del chat.
      </p>
      <ProfileForm initial={profile} />
    </section>
  );
}
