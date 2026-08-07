"use client";

import { createContext, useContext, useMemo, useState } from "react";

type AuthInteraction = {
  typing: boolean;
  hasPassword: boolean;
  passwordVisible: boolean;
  setTyping: (value: boolean) => void;
  setHasPassword: (value: boolean) => void;
  setPasswordVisible: (value: boolean) => void;
};

const noop = () => {};

const AuthInteractionContext = createContext<AuthInteraction>({
  typing: false,
  hasPassword: false,
  passwordVisible: false,
  setTyping: noop,
  setHasPassword: noop,
  setPasswordVisible: noop,
});

export function AuthInteractionProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [typing, setTyping] = useState(false);
  const [hasPassword, setHasPassword] = useState(false);
  const [passwordVisible, setPasswordVisible] = useState(false);

  const value = useMemo(
    () => ({
      typing,
      hasPassword,
      passwordVisible,
      setTyping,
      setHasPassword,
      setPasswordVisible,
    }),
    [typing, hasPassword, passwordVisible]
  );

  return (
    <AuthInteractionContext.Provider value={value}>
      {children}
    </AuthInteractionContext.Provider>
  );
}

export function useAuthInteraction() {
  return useContext(AuthInteractionContext);
}
