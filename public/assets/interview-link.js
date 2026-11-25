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
  if(check!==accessCheck)return;
  interviewLink.hidden=!data.allowed;
  if(data.allowed && new URLSearchParams(location.search).get('interviews')==='1'){
   location.replace('/ops/interviews');
  }
 }catch{}
}
new MutationObserver(refreshInterviewLink).observe(logoutButton,{attributes:true,attributeFilter:['hidden']});
refreshInterviewLink();
