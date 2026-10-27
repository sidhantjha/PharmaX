// Adapted from useLayouts components/ui/{sheet,dropdown-menu}.tsx (MIT).
// The Base UI composition is retained; Tailwind and icon dependencies are replaced.
import type { ReactNode } from 'react';
import { Dialog } from '@base-ui/react/dialog';
import { Menu } from '@base-ui/react/menu';
import { X, ChevronDown } from 'lucide-react';

export function Sheet({ open, onOpenChange, title, description, children, side = 'right', modal = true }: {
  open: boolean; onOpenChange: (open: boolean) => void; title: string;
  description: string; children: ReactNode; side?: 'right' | 'bottom'; modal?: boolean;
}) {
  return <Dialog.Root open={open} onOpenChange={onOpenChange} modal={modal}>
    <Dialog.Portal>{modal&&<Dialog.Backdrop className="sheet-backdrop" />}
      <Dialog.Popup className={`sheet sheet-${side}`}>
        <header className="sheet-heading"><div><Dialog.Title>{title}</Dialog.Title>
          <Dialog.Description>{description}</Dialog.Description></div>
          <Dialog.Close className="icon-button" aria-label="Close panel"><X size={18}/></Dialog.Close>
        </header>{children}
      </Dialog.Popup>
    </Dialog.Portal>
  </Dialog.Root>;
}

export function Dropdown({ label, items }: {label: string; items: {label: string; action: () => void}[]}) {
  return <Menu.Root><Menu.Trigger className="dropdown-trigger">{label}<ChevronDown size={13}/></Menu.Trigger>
    <Menu.Portal><Menu.Positioner sideOffset={8} className="menu-positioner">
      <Menu.Popup className="dropdown-menu">{items.map(item => <Menu.Item key={item.label} onClick={item.action}>{item.label}</Menu.Item>)}</Menu.Popup>
    </Menu.Positioner></Menu.Portal>
  </Menu.Root>;
}
