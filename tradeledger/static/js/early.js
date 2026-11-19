// Runs before the page is painted (loaded without "defer" in <head>).
// Adding class="js" lets CSS switch to JavaScript-enhanced layouts, such as
// the collapsible mobile menu, without a visible jump. If JavaScript is off,
// the class is never added and the plain, fully working layout is used.
document.documentElement.classList.add("js");
