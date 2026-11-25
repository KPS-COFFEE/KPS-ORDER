'use strict';
const interviewLink=document.getElementById('interview-link');
const logoutButton=document.getElementById('logout');
let accessCheck=0;
async function refreshInterviewLink(){
 const check=++accessCheck;
 interviewLink.hidden=true;
 if(logoutButton.hidden)return;
 try{
  const response=await fetch('/api/interviews/access',{cache:'no-store'});
  const data=await response.json();
  if(check===accessCheck)interviewLink.hidden=!data.allowed;
 }catch{}
}
new MutationObserver(refreshInterviewLink).observe(logoutButton,{attributes:true,attributeFilter:['hidden']});
refreshInterviewLink();
