import u from './Util';
import _ from './utils/I18n';
import icon from './Icon'

u.ready(function () {
  u.each('.markdown-editor', function (el, i) {
    initializeEditor(el);
  })
});

function makeThingy(name, title, fn) {
  var x = document.createElement("div");
  x.title = title, x.className = name;
  x.setAttribute('data-icon', name);
  x.innerHTML = icon[name];
  x.onclick = fn;
  return x;
}


function initializeEditor(element) {
  var el = document.createElement("div");
  var textarea = element.children[0];
  el.classList.add('editbtns');

    el.appendChild(makeThingy('bold', _('Bold (ctrl-b)'), function(e){addTagsEnclosingEachLineInSelection(textarea, '**', '**');}));
    el.appendChild(makeThingy('italic', _('Italic (ctrl-i)'), function(e){addTagsEnclosingEachLineInSelection(textarea, '*', '*');}));
    el.appendChild(makeThingy('strikethrough',  _('Strikethrough (ctrl-shift-s)'), function(e){addTagsEnclosingEachLineInSelection(textarea, '~~', '~~');}));
    el.appendChild(makeThingy('title',  _('Title (ctrl-shift-h)'), function(e){addTagForTitle(textarea);}));
    el.appendChild(makeThingy('gradient',  _('Spoiler'), function(e){addTagsEnclosingEachLineInSelection(textarea, ">!", "!<");}));


  var x = document.createElement('span');
  x.className = 'separator';
  el.appendChild(x);

  var makeLink = function (e) {
    var uri = prompt(_('Insert hyperlink'));
    if (uri) {
      if (getCursorSelection(textarea)[1] == '') {
        addTags(textarea, '[', _('Link Title'), '](' + uri + ')');
      } else {
        addTags(textarea, '[', '](' + uri + ')');
      }
    }
  }

  el.appendChild(makeThingy('link', _('Insert link (ctrl-shift-k)'), makeLink));

  x = document.createElement('span');
  x.className = 'separator';
  el.appendChild(x);

  el.appendChild(makeThingy('bulletlist', _('Bullet list'), function (e) { addTags(textarea, '- ', ''); }));
//  el.appendChild(makeThingy('numberlist', _('Number list'), function (e) { addTags(textarea, '1. ', ''); }));

  x = document.createElement('span');
  x.className = 'separator';
  el.appendChild(x);

    el.appendChild(makeThingy('code', _('Code'), function(e){addTagsForCode(textarea);}));
    el.appendChild(makeThingy('quote', _('Quote (ctrl-shift-.)'), function(e){addTagsForQuotes(textarea);}));

  var x = document.createElement('span');
  x.className = 'separator';
  el.appendChild(x);

//  var makeImgLink = function (e) {
//    var uri = prompt(_('Insert image link'));
//    if (uri) {
//      if (getCursorSelection(textarea)[1] == '') {
//        addTags(textarea, '![', _('Link Title'), '](' + uri + ')');
//      } else {
//        addTags(textarea, '![', '](' + uri + ')');
//      }
//    }
//  }
//
//  el.appendChild(makeThingy('image', _('Insert image link'), makeImgLink));

    // Add drag and drop handlers to textarea
    const setupDragAndDrop = (textarea) => {
        textarea.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.stopPropagation();
            textarea.classList.add('dragover'); // Add a visual indicator
        });

        textarea.addEventListener('dragleave', (e) => {
            e.preventDefault();
            e.stopPropagation();
            textarea.classList.remove('dragover');
        });

        textarea.addEventListener('drop', (e) => {
            e.preventDefault();
            e.stopPropagation();
            textarea.classList.remove('dragover');

            const files = e.dataTransfer.files;
            if (files.length === 0) return;

            // Use the first file only
            const file = files[0];

            // Check if it's an image
            if (!file.type.startsWith("image/")) {
                alert(_('Invalid file type. Please select an image.'));
                return;
            }

            // Check file size
            if (file.size > 5 * 1024 * 1024) {
                alert(_('Image must be smaller than 5MB'));
                return;
            }

            // Check existing image count
            const imageCount = (textarea.value.match(/!\[.*?\]\(.*?\)/g) || []).length;
            if (imageCount >= 1) {
                alert(_('Only 1 image allowed'));
                return;
            }

            // Get post ID if available
            const postDiv = textarea.closest('.wholepost');
            const pid = postDiv && postDiv.querySelector('.postbar')
                ? postDiv.querySelector('.postbar').getAttribute('pid')
                : null;

            // Upload the file
            const formData = new FormData();
            formData.append("files", file);
            if (pid) formData.append("pid", pid);

            fetch('/do/upload_image', {
                method: 'POST',
                body: formData
            })
            .then(response => response.json())
            .then(data => {
                if (data.status === 'ok') {
                    // Insert at cursor position or at end if no selection
                    const [before, selected, after] = getCursorSelection(textarea);
                    const altText = selected.trim() ? selected : _('Image Description');
                    textarea.value = before + `![${altText}](${data.image_url})` + after;

                    // Update upload button state
                    const uploadButton = textarea.closest('form').querySelector('div[data-icon="image"]');
                    if (uploadButton) {
                        uploadButton.style.opacity = '0.5';
                        uploadButton.style.pointerEvents = 'none';
                        uploadButton.title = _('Only 1 image allowed');
                    }
                } else {
                    alert(_('Error uploading image: ') + data.error);
                }
            })
            .catch(error => {
                console.error('Upload error:', error);
                alert(_('Error uploading image'));
            });
        });
    };

    // Add some CSS for the drag indicator
    const style = document.createElement('style');
    style.textContent = `
        textarea.dragover {
            border: 2px dashed #666 !important;
            background-color: rgba(0,0,0,0.05);
        }
    `;
    document.head.appendChild(style);

    var makeImgUpload = function (e) {
        var imageCount = (textarea.value.match(/!\[.*?\]\(.*?\)/g) || []).length;
        if (imageCount >= 1) {
            alert(_('Only 1 image allowed'));
            return;
        }

        var postDiv = e.target.closest('.wholepost');
        var pid = postDiv && postDiv.querySelector('.postbar')
            ? postDiv.querySelector('.postbar').getAttribute('pid')
            : null;

        var fileInput = document.createElement("input");
        fileInput.type = "file";
        fileInput.accept = "image/*";

        var uploadButton = e.target.closest('div[data-icon="image"]');

        fileInput.onchange = function () {
            var file = fileInput.files[0];

            // Validate file size (e.g., max 5MB)
            if (file.size > 5 * 1024 * 1024) {
                alert(_('Image must be smaller than 5MB'));
                return;
            }

            // Validate file type
            if (!file.type.startsWith("image/")) {
                alert(_('Invalid file type. Please select an image.'));
                return;
            }

            var formData = new FormData();
            formData.append("files", file);
            if (pid) formData.append("pid", pid);

            fetch('/do/upload_image', {
                method: 'POST',
                body: formData
            })
            .then(response => response.json())
            .then(data => {
                if (data.status === 'ok') {
                    var [before, selected, after] = getCursorSelection(textarea);
                    var altText = selected.trim() ? selected : 'image';
                    textarea.value = before + `![${altText}](${data.image_url})` + after;

                    // Restore cursor position
                    var newCursorPos = before.length + `![${altText}](`.length;
                    setSelection(textarea, newCursorPos, newCursorPos);

                    // Disable upload button after success
                    if (uploadButton) {
                        uploadButton.style.opacity = '0.5';
                        uploadButton.style.pointerEvents = 'none';
                        uploadButton.title = _('Only 1 image allowed');
                    }
                } else {
                    alert(_('Error uploading image: ') + data.error);

                    // Restore upload button if an error occurs
                    if (uploadButton) {
                        uploadButton.style.opacity = '1';
                        uploadButton.style.pointerEvents = 'auto';
                        uploadButton.title = _('Upload image');
                    }
                }
            })
            .catch(error => {
                console.error('Upload error:', error);
                alert(_('Error uploading image'));

                // Restore upload button on failure
                if (uploadButton) {
                    uploadButton.style.opacity = '1';
                    uploadButton.style.pointerEvents = 'auto';
                    uploadButton.title = _('Upload image');
                }
            });
        };

        fileInput.click();
    };

    // Append the upload button
    el.appendChild(makeThingy('image', _('Upload image'), makeImgUpload));
    setupDragAndDrop(textarea);

  element.insertBefore(el, element.firstChild);

  window.onkeydown = function (e) {
    if (e.shiftKey && e.altKey && e.which == 67) {
      var te = document.getElementById('title');
      if (!te || te.value.length == 0) { return; }
      te.value = te.value.charAt(0).toUpperCase() + te.value.slice(1).toLowerCase();
    }
    if (textarea !== document.activeElement) { return; }
    if (e.ctrlKey == true && e.which == 66) {
      addTagsEnclosingEachLineInSelection(textarea, '**', '**'); e.preventDefault();
    } else if (e.ctrlKey == true && e.shiftKey == true && e.which == 73) {
      addTagsEnclosingEachLineInSelection(textarea, '*', '*'); e.preventDefault(); return false;
    } else if (e.ctrlKey == true && e.shiftKey == true && e.which == 83) {
      addTagsEnclosingEachLineInSelection(textarea, '~~', '~~'); e.preventDefault();
    } else if (e.ctrlKey == true && e.shiftKey == true && e.which == 72) {
      addTagForTitle(textarea); e.preventDefault();
    } else if (e.ctrlKey == true && e.shiftKey == true && e.which == 75) {
      makeLink(e); e.preventDefault();
    } else if (e.ctrlKey == true && e.shiftKey == true && e.which == 75) {
      makeImgUpload(e); e.preventDefault();
    } else if (e.ctrlKey == true && e.shiftKey == true && e.which == 190) {
      addTagsForQuotes(textarea); e.preventDefault();
    }
  }
}


function addTags(textarea, begin, end, bm) {
  var sel = getCursorSelection(textarea);
  if (bm) {
    var rbm = begin;
    begin = begin + end;
    end = bm;
  }
  textarea.value = sel[0] + begin + sel[1] + end + sel[2];
  var u = sel[0].length + begin.length + sel[1].length;
  if (bm) {
    setSelection(textarea, rbm.length + sel[0].length, u);
  } else {
    setSelection(textarea, u, u);
  }
}


function addTagForTitle(textarea) {
  const [beforeText, selectedText, afterText] = getExpandedSelection(textarea);
  const modifiedText = "# " + selectedText;
  setTextAndUpdateCursor(textarea, beforeText, modifiedText, afterText);
}

function addTagsForQuotes(textarea) {
  const [beforeText, selectedText, afterText] = getExpandedSelection(textarea);
  const modifiedText = selectedText.replace(/^(.*)$/gm, "> $1");
  setTextAndUpdateCursor(textarea, beforeText, modifiedText, afterText);
}

function addTagsEnclosingEachLineInSelection(textarea, prefix, suffix) {
  const [beforeText, selectedText, afterText] = getCursorSelection(textarea);
  const modifiedText = selectedText.replace(/^(.+)$/gm, prefix + "$1" + suffix);
  setTextAndUpdateCursor(textarea, beforeText, modifiedText, afterText);
}

function addTagsForCode(textarea) {
  let [beforeText, selectedText, afterText] = getCursorSelection(textarea);
  let modifiedText;
  if (selectedText.includes("\n")) {
    [beforeText, selectedText, afterText] = getExpandedSelection(textarea);
    modifiedText = "```\n" + selectedText + "\n```";
  } else {
    modifiedText = "`" + selectedText + "`";
  }
  setTextAndUpdateCursor(textarea, beforeText, modifiedText, afterText);
}

/* Drops the cursor just inside the end of the selection. */
function setTextAndUpdateCursor(textarea, beforeText, modifiedText, afterText) {
  textarea.value = beforeText + modifiedText + afterText;
  const cursorIndex = beforeText.length + modifiedText.length;
  setSelection(textarea, cursorIndex, cursorIndex);
}

/* Expands the selection to encompass the beginning of the first line selected and the end of the last line selected. */
function getExpandedSelection(textarea) {
  let i = textarea.selectionStart;
  let n = textarea.selectionEnd;
  while (i > 0 && textarea.value.charAt(i - 1) !== "\n") {
    i--;
  }
  while (n < textarea.value.length && textarea.value.charAt(n) !== "\n") {
    n++;
  }
  return [textarea.value.substring(0,i),
          textarea.value.substring(i,n),
          textarea.value.substring(n, textarea.value.length)];
}


function getCursorSelection(textarea) {
  var i = textarea.selectionStart;
  var n = textarea.selectionEnd;
  return [textarea.value.substring(0, i),
  textarea.value.substring(i, n),
  textarea.value.substring(n, textarea.value.length)];
}

function setSelection(textarea, begin, end) {
  const scrollPos = textarea.scrollTop; // Store current scroll position
  if (textarea.setSelectionRange) {
    textarea.setSelectionRange(begin, end);
  } else if (textarea.createTextRange) {
    var tra = textarea.createTextRange();
    tra.collapse(0);
    tra.moveEnd('character', end);
    tra.moveStart('character', begin);
    tra.select();
  }
  textarea.scrollTop = scrollPos; // Restore scroll position after selection update
}

export default initializeEditor;
