// How to export from Instagram: the dialog.
import { openDialog } from "./dialogs.js";
import { $, $dialog } from "./dom.js";
import { fileInput } from "./upload.js";

const howtoDlg = $dialog("howto");
export function openHowto() { openDialog(howtoDlg); }
$("howto-close").addEventListener("click", () => howtoDlg.close());
$("howto-pick").addEventListener("click", () => { howtoDlg.close(); fileInput.click(); });
