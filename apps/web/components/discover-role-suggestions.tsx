"use client";
import { useEffect, useRef, useState } from "react";
import { webRequest } from "../lib/client";

export function DiscoverRoleSuggestions({
  onSelect,
}: {
  onSelect: (role: string) => void;
}) {
  const [roles, setRoles] = useState<string[]>([]);
  const request = useRef<Promise<string[]> | null>(null);
  useEffect(() => {
    let active = true;
    // Reattach on StrictMode effect replay without a second Profile GET.
    request.current ??= webRequest<{ target_roles?: unknown }>("/api/profile")
      .then((profile) =>
        Array.isArray(profile?.target_roles)
          ? profile.target_roles.filter(
              (role): role is string =>
                typeof role === "string" &&
                role.trim().length > 0 &&
                role.length <= 200,
            )
          : [],
      )
      .catch(() => []);
    void request.current.then((items) => {
      if (active) setRoles(items);
    });
    return () => {
      active = false;
    };
  }, []);
  if (!roles.length) return null;
  return (
    <div className="discover-roles">
      <span className="muted">Роли из профиля</span>
      <div className="action-suggestions" aria-label="Роли из профиля">
        {roles.map((role, index) => (
          <button
            key={`${index}:${role}`}
            type="button"
            onClick={() => onSelect(role)}
          >
            {role}
          </button>
        ))}
      </div>
    </div>
  );
}
