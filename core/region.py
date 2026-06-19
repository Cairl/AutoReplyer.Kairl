"""
Screen region selector: full-screen tkinter overlay for drag-to-select.
"""

import pyautogui


class RegionSelector:
    """
    Full-screen screenshot overlay for selecting screen regions.
    User drags a rectangle, returns {x, y, w, h}.
    Returns None if cancelled (Escape).
    """

    DIM_COLOR = "#1e1e2e"
    RECT_COLOR = "#89B4FA"
    TEXT_COLOR = "#cdd6f4"
    HINT_COLOR = "#f9e2af"

    def select(self, title: str = "拖拽选择区域") -> dict | None:
        try:
            import tkinter as tk
        except ImportError:
            return None

        result = {"rect": None}

        screenshot = pyautogui.screenshot()

        root = tk.Tk()
        root.title(title)
        root.attributes("-fullscreen", True)
        root.attributes("-topmost", True)

        from PIL import ImageTk
        photo = ImageTk.PhotoImage(screenshot, master=root)

        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()

        root.photo = photo  # prevent garbage collection

        canvas = tk.Canvas(root, cursor="cross", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        canvas.create_image(0, 0, anchor="nw", image=photo)

        hint_id = canvas.create_text(
            screen_w // 2, 30,
            text=f"{title}  |  拖拽选择  |  ESC 取消",
            fill=self.HINT_COLOR, font=("Consolas", 14)
        )

        state = {
            "x0": 0, "y0": 0,
            "rect_id": None,
            "dim_ids": [],
            "text_id": None,
        }

        def clear_overlay():
            for rid in state["dim_ids"]:
                canvas.delete(rid)
            state["dim_ids"] = []
            if state["text_id"]:
                canvas.delete(state["text_id"])
                state["text_id"] = None

        def update_overlay(x1, y1, x2, y2):
            for rid in state["dim_ids"]:
                canvas.delete(rid)
            state["dim_ids"] = [
                canvas.create_rectangle(0, 0, screen_w, y1, fill=self.DIM_COLOR, outline="", stipple="gray50"),
                canvas.create_rectangle(0, y2, screen_w, screen_h, fill=self.DIM_COLOR, outline="", stipple="gray50"),
                canvas.create_rectangle(0, y1, x1, y2, fill=self.DIM_COLOR, outline="", stipple="gray50"),
                canvas.create_rectangle(x2, y1, screen_w, y2, fill=self.DIM_COLOR, outline="", stipple="gray50"),
            ]
            w, h = x2 - x1, y2 - y1
            if state["text_id"]:
                canvas.delete(state["text_id"])
            if w > 40 and h > 20:
                state["text_id"] = canvas.create_text(
                    x1 + w // 2, y1 + h // 2,
                    text=f"{w} × {h}",
                    fill=self.TEXT_COLOR, font=("Consolas", 12)
                )
            else:
                state["text_id"] = None
            if state["rect_id"]:
                canvas.tag_raise(state["rect_id"])
            if state["text_id"]:
                canvas.tag_raise(state["text_id"])
            canvas.tag_raise(hint_id)

        def on_press(event):
            state["x0"] = event.x
            state["y0"] = event.y
            if state["rect_id"]:
                canvas.delete(state["rect_id"])
            clear_overlay()
            state["rect_id"] = canvas.create_rectangle(
                event.x, event.y, event.x, event.y,
                outline=self.RECT_COLOR, width=2
            )

        def on_drag(event):
            if not state["rect_id"]:
                return
            x1 = min(state["x0"], event.x)
            y1 = min(state["y0"], event.y)
            x2 = max(state["x0"], event.x)
            y2 = max(state["y0"], event.y)
            canvas.coords(state["rect_id"], x1, y1, x2, y2)
            update_overlay(x1, y1, x2, y2)

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
