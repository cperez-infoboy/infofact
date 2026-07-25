// Store de autenticación. Archivo .ts PLANO: usa writable de svelte/store.
// Las runes ($state/$derived/$effect) están PROHIBIDAS aquí — solo en .svelte
// y .svelte.ts. El build no avisa; falla en runtime.
import { writable } from 'svelte/store';
import type { User } from '$lib/api/auth';
import * as authApi from '$lib/api/auth';

export const authStore = writable<User | null>(null);

// Carga el usuario actual al montar la app. 401 → store en null (sin sesión).
export async function loadCurrentUser(): Promise<void> {
  try {
    authStore.set(await authApi.me());
  } catch {
    authStore.set(null);
  }
}
