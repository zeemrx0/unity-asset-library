import { useLayoutEffect, useRef } from "react";
import { setInteractionModality, useFocusVisible } from "@react-aria/interactions";

// Chromium treats Command alone as keyboard input. Use React Aria's modality
// tracking so app switching cannot outline a pointer-focused control.
export function FocusVisibility() {
  const { isFocusVisible } = useFocusVisible();
  const focusVisible = useRef(isFocusVisible);
  useLayoutEffect(() => {
    let escapeFromPointer = false;
    const rememberModality = (event: KeyboardEvent) => {
      if (event.key === "Escape") escapeFromPointer = !focusVisible.current;
    };
    const preserveModality = (event: KeyboardEvent) => {
      if (event.key === "Escape" && escapeFromPointer) setInteractionModality("pointer");
    };
    // Record before React Aria's document listener; restore after it on both
    // keydown and keyup. Escape dismisses UI but does not begin navigation.
    window.addEventListener("keydown", rememberModality, true);
    document.addEventListener("keydown", preserveModality, true);
    document.addEventListener("keyup", preserveModality, true);
    return () => {
      window.removeEventListener("keydown", rememberModality, true);
      document.removeEventListener("keydown", preserveModality, true);
      document.removeEventListener("keyup", preserveModality, true);
    };
  }, []);
  useLayoutEffect(() => {
    focusVisible.current = isFocusVisible;
    document.documentElement.dataset.focusVisible = String(isFocusVisible);
    return () => { delete document.documentElement.dataset.focusVisible; };
  }, [isFocusVisible]);
  return null;
}
