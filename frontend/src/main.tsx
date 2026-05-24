import { createElement } from "react";
import { createRoot } from "react-dom/client";
import { app_shell } from "./app";
import "./styles.css";

const root_element = document.getElementById("root");

if (!root_element) {
  throw new Error("root element was not found");
}

createRoot(root_element).render(createElement(app_shell));
