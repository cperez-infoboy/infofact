// Wrapper de fetch con auth por cookie (HttpOnly, same-origin vía proxy).
// `credentials: 'include'` SIEMPRE: la cookie JWT vive en el browser y debe
// viajar en cada request.

const BASE = ''; // same-origin (proxy en dev, FastAPI en prod)

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message?: string
  ) {
    super(message ?? code);
    this.name = 'ApiError';
  }
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const { headers, ...rest } = init ?? {};
  // FormData: no forzamos Content-Type para que el browser fije el boundary
  // multipart automáticamente. Cualquier otro body viaja como JSON.
  const isForm = init?.body instanceof FormData;
  const res = await fetch(BASE + path, {
    credentials: 'include',
    headers: {
      ...(isForm ? {} : { 'Content-Type': 'application/json' }),
      ...(headers as Record<string, string> | undefined)
    },
    ...rest
  });

  if (!res.ok) {
    let code = 'request_failed';
    try {
      const body = await res.json();
      // FastAPI devuelve {detail: "..."} en los HTTPException.
      code = body.detail ?? code;
    } catch {
      // Respuesta no-JSON; nos quedamos con el code por defecto.
    }
    throw new ApiError(res.status, code);
  }

  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}
