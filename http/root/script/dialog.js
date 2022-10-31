// JS code for individual dialog boxes.
//
//      Connor Shugg

// Globals
const body = document.getElementsByTagName("body")[0];
let current_dialog = null;

// Main card class.
class Dialog
{
    // Constructor. Takes in an ID.
    constructor(id)
    {
        this.id = id;

        this.html = document.createElement("dialog");
        this.html.className = "mdl-dialog";
        this.html.className += " mdl-color--grey-900";
    
        // create the dialog title
        const dtitle = document.createElement("h4");
        dtitle.className = "mdl-dialog__title";
        dtitle.className += " mdl-color-text--grey-100";
        dtitle.innerHTML = "";
        this.html.appendChild(dtitle);
        this.html_title = dtitle;
    
        // create the dialog message
        const dcontent = document.createElement("div");
        dcontent.className = "mdl-dialog__content";
        const dcontent_p = document.createElement("p");
        dcontent_p.className = "mdl-color-text--grey-400";
        dcontent_p.innerHTML = ""
        dcontent.appendChild(dcontent_p);
        this.html.appendChild(dcontent);
        this.html_content = dcontent_p;
        
        // create the action container
        const dactions = document.createElement("div");
        dactions.className = "mdl-dialog__actions";
        this.html.appendChild(dactions);
        this.html_actions = dactions;

    }

    // Sets the dialog's title.
    set_title(text)
    {
        this.html_title.innerHTML = text;
    }
    
    // Sets the dialog's message.
    set_message(text)
    {
        this.html_content.innerHTML = text;
    }

    // Adds a new action to the dialog box.
    add_action(name, handler)
    {
        const btn = document.createElement("button");
        button_init(btn);
        btn.innerHTML = name;
        btn.addEventListener("click", handler);
        this.html_actions.appendChild(btn);
    }
    
    // Shows the dialog on the screen.
    show()
    {
        body.appendChild(this.html);
        current_dialog = this;
        return this.html.showModal();
    }
    
    // Closes the dialog.
    close()
    {
        if (current_dialog == null)
        { return; }

        // if 'this' refers to a button that was clicked, we'll reference
        // 'current_dialog' instead
        let dlg = this;
        if (typeof dlg != Dialog)
        { dlg = current_dialog; }
        
        // remove the dialog's HTML from the document, reset the global,
        // and close the dialog
        body.removeChild(dlg.html);
        current_dialog = null;
        dlg.html.close();
    }
}

