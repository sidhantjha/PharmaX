// Adapted from @openuidev/react-ui 0.18.1, src/components/Input/Input.tsx.
// MIT, Copyright (c) 2011-2024 Thesys Inc. See THIRD_PARTY_NOTICES.md.
// Omitted the optional FormControl context and replaced clsx with local classes.
import React from 'react';

export interface InputProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'size'> {
  styles?: React.CSSProperties;
  size?: 'small' | 'medium' | 'large';
  hasError?: boolean;
}
const sizes = {small:'openui-input-small',medium:'openui-input-medium',large:'openui-input-large'};
export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, styles, size='medium', hasError=false, ...props }, ref) => (
    <input autoComplete="off" ref={ref}
      className={['openui-input',sizes[size],className,hasError?'openui-input-error':''].filter(Boolean).join(' ')}
      style={styles} {...props}/>
  ),
);
Input.displayName='Input';
