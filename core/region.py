"""
Screen region selector: full-screen tkinter overlay for drag-to-select.
"""

import pyautogui


class RegionSelector:
    """
    Full-screen screenshot overlay for selecting screen regions.
    Opens a tkinter window, user drags a rectangle, returns (x, y, w, h).
    Returns None if cancelled (Escape).
    """

    def select(self, title: str = "拖拽选择区域") -> dict | None:
        try:
            import tkinter as tk
        except ImportError:
            return None

        result = {"rect": None}

        # Capture screen
        screenshot = pyautogui.screenshot()

        root = tk.Tk()
        root.title(title)
        root.attributes("-fullscreen", True)
        root.attributes("-topmost", True)

        from PIL import ImageTk
        photo = ImageTk.PhotoImage(screenshot)

        canvas = tk.Canvas(root, cursor="cross", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        canvas.create_image(0, 0, anchor="nw", image=photo)

        # Semi-transparent overlay hint
        canvas.create_text(
            root.winfo_screenwidth() // 2, 30,
            text=f"{title}  |  拖拽选择  |  ESC 取消",
            fill="white", font=("Consolas", 14)
        )

        state = {"x0": 0, "y0": 0, "rect_id": None}

        def on_press(event):
            state["x0"] = event.x
            state["y0"] = event.y
            if state["rect_id"]:
                canvas.delete(state["rect_id"])
            state["rect_id"] = canvas.create_rectangle(
                event.x, event.y, event.x, event.y,
                outline="#89B4FA", width=2, dash=(6, 4)
            )

        def on_drag(event):
            if state["rect_id"]:
                canvas.coords(state["rect_id"], state["x0"], state["y0"], event.x, event.y)

        def on_release(event):
            x1, y1 = min(state["x0"], event.x), min(state["y0"], event.y)
            x2, y2 = max(state["x0"], event.x), max(state["y0"], event.y)
            w, h = x2 - x1, y2 - y1
            if w > 10 and h > 10:
                result["rect"] = {"x": x1, "y": y1, "w": w, "h": h}
            root.destroy()

        def on_escape(event):
            root.destroy()

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        root.bind("<Escape>", on_escape)

        root.mainloop()
        return result["rect"]
