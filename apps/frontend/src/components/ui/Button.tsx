import { forwardRef, type ButtonHTMLAttributes } from "react";
import styles from "./Button.module.css";

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger" | "ghost";
  size?: "sm" | "md";
};

export const Button = forwardRef<HTMLButtonElement, Props>(
  function Button({ variant = "primary", size = "md", className = "", children, ...rest }, ref) {
    const cls = [styles.btn, styles[variant], styles[size], className].filter(Boolean).join(" ");
    return (
      <button ref={ref} className={cls} {...rest}>
        {children}
      </button>
    );
  }
);
