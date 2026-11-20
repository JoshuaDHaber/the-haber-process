"""Builds pwa/add-slip.shortcut, the iPhone Shortcut "Add Link or Img to The Haber Process". It's in the share menu for screenshots,
links and text. A screenshot's text is read on the phone (Apple's Extract Text from Image); a link or text, such as
FanDuel's "Check out my bet" share, is passed on as it is (the server reads a FanDuel share link: backend share_links).
Either way it opens My bets with it (/bets#slip=<text>), where Add a bet fills in from it (frontend bets.holdSharedText).
iPhones can't share to a web app, hence the Shortcut.

Rebuild (on a Mac signed in to iCloud) when the site's address or these steps change:
    python3 scripts/build-shortcut.py https://process.jdhaber.com /tmp/unsigned.shortcut
    shortcuts sign --mode anyone --input /tmp/unsigned.shortcut --output pwa/add-slip.shortcut
"anyone" signing carries an anonymous Apple certificate ID, not the signer's name or Apple ID.

Add a bet links to the Shortcut's iCloud link (AddBetSheet SHORTCUT), not the file: after a rebuild, add the new file in
Shortcuts on a Mac, then Share → Copy iCloud Link, and put that link in SHORTCUT. Shortcuts names an imported Shortcut after
its file, not WFWorkflowName, and that name is what the share menu shows: open a copy named for it, replacing the old one:
    cp pwa/add-slip.shortcut "/tmp/Add Link or Img to The Haber Process.shortcut" && open -a Shortcuts "/tmp/Add Link or Img to The Haber Process.shortcut"
"""
import plistlib, sys, uuid
site = sys.argv[1]
new = lambda: str(uuid.uuid4()).upper()  # noqa: E731
imgs, ocr, txt, branch, joined, comb, enc = new(), new(), new(), new(), new(), new(), new()
prefix = f"{site}/bets#slip="
def out(u, name): return {"Type": "ActionOutput", "OutputUUID": u, "OutputName": name}
def var(u, name): return {"Value": out(u, name), "WFSerializationType": "WFTextTokenAttachment"}
def text_of(u, name): return {"Value": {"string": "￼", "attachmentsByRange": {"{0, 1}": out(u, name)}}, "WFSerializationType": "WFTextTokenString"}
SHARED = {"Value": {"Type": "ExtensionInput"}, "WFSerializationType": "WFTextTokenAttachment"}
wf = {
    "WFWorkflowClientVersion": "2607.0.2", "WFWorkflowMinimumClientVersion": 900, "WFWorkflowMinimumClientVersionString": "900",
    "WFWorkflowName": "Add Link or Img to The Haber Process",
    "WFWorkflowIcon": {"WFWorkflowIconStartColor": 463140863, "WFWorkflowIconGlyphNumber": 59511},
    "WFWorkflowTypes": ["ActionExtension"],
    "WFWorkflowInputContentItemClasses": ["WFImageContentItem", "WFURLContentItem", "WFStringContentItem", "WFRichTextContentItem"],
    "WFWorkflowHasShortcutInputVariables": True, "WFWorkflowImportQuestions": [], "WFQuickActionSurfaces": [],
    "WFWorkflowActions": [
        # a screenshot in what was shared? (a share can carry an image, a link and text together)
        {"WFWorkflowActionIdentifier": "is.workflow.actions.detect.images",
         "WFWorkflowActionParameters": {"UUID": imgs, "WFInput": SHARED}},
        {"WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
         "WFWorkflowActionParameters": {"GroupingIdentifier": branch, "WFControlFlowMode": 0, "WFCondition": 100,   # has any value
                                        "WFInput": {"Type": "Variable", "Variable": var(imgs, "Images")}}},
        # yes: read its text on the phone
        {"WFWorkflowActionIdentifier": "is.workflow.actions.extracttextfromimage",
         "WFWorkflowActionParameters": {"UUID": ocr, "WFImage": var(imgs, "Images")}},
        {"WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
         "WFWorkflowActionParameters": {"GroupingIdentifier": branch, "WFControlFlowMode": 1}},
        # no: the shared link or text as it is (FanDuel's share is a link and a line of text)
        {"WFWorkflowActionIdentifier": "is.workflow.actions.detect.text",
         "WFWorkflowActionParameters": {"UUID": txt, "WFInput": SHARED}},
        {"WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
         "WFWorkflowActionParameters": {"GroupingIdentifier": branch, "WFControlFlowMode": 2, "UUID": joined}},
        # several items (two screenshots, a link and its text) become one block of text, a line each
        {"WFWorkflowActionIdentifier": "is.workflow.actions.text.combine",
         "WFWorkflowActionParameters": {"UUID": comb, "WFTextSeparator": "New Lines", "text": var(joined, "If Result")}},
        {"WFWorkflowActionIdentifier": "is.workflow.actions.urlencode",
         "WFWorkflowActionParameters": {"UUID": enc, "WFInput": text_of(comb, "Combined Text")}},
        {"WFWorkflowActionIdentifier": "is.workflow.actions.openurl",
         "WFWorkflowActionParameters": {"WFInput": {"Value": {"string": prefix + "￼",
                                                              "attachmentsByRange": {f"{{{len(prefix)}, 1}}": out(enc, "URL Encoded Text")}},
                                                    "WFSerializationType": "WFTextTokenString"}}},
    ],
}
plistlib.dump(wf, open(sys.argv[2], "wb"), fmt=plistlib.FMT_BINARY)
