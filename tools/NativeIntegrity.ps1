# File labels, rather than a successful compile or SHA256, determine the token
# of a newly launched desktop process. See docs/CURRENT-IMPLEMENTATION.zh-CN.md.
if (-not ('AIPraat.NativeIntegrity' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
namespace AIPraat {
 public static class NativeIntegrity {
  [DllImport("advapi32.dll", CharSet=CharSet.Unicode)] static extern uint GetNamedSecurityInfoW(string name,int type,uint information,out IntPtr owner,out IntPtr group,out IntPtr dacl,out IntPtr sacl,out IntPtr descriptor);
  [DllImport("advapi32.dll")] static extern bool GetAce(IntPtr acl,uint index,out IntPtr ace);
  [DllImport("advapi32.dll")] static extern IntPtr GetSidSubAuthorityCount(IntPtr sid);
  [DllImport("advapi32.dll")] static extern IntPtr GetSidSubAuthority(IntPtr sid,uint index);
  [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr memory);
  [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr OpenProcess(uint access,bool inherit,int pid);
  [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
  [DllImport("advapi32.dll",SetLastError=true)] static extern bool OpenProcessToken(IntPtr process,uint access,out IntPtr token);
  [DllImport("advapi32.dll",SetLastError=true)] static extern bool GetTokenInformation(IntPtr token,int kind,IntPtr buffer,uint size,out uint needed);
  static int Rid(IntPtr sid) { uint count=Marshal.ReadByte(GetSidSubAuthorityCount(sid)); return Marshal.ReadInt32(GetSidSubAuthority(sid,count-1)); }
  public static int FileLevel(string path) {
   IntPtr owner,group,dacl,sacl,descriptor;
   uint error=GetNamedSecurityInfoW(path,1,0x10,out owner,out group,out dacl,out sacl,out descriptor);
   if(error!=0) throw new Win32Exception((int)error);
   try {
    // An absent mandatory-label ACE is Medium, not Low or "unknown".
    if(sacl!=IntPtr.Zero) for(uint i=0;i<(ushort)Marshal.ReadInt16(sacl,4);i++) {
     IntPtr ace; if(!GetAce(sacl,i,out ace)) throw new Win32Exception(Marshal.GetLastWin32Error());
     if(Marshal.ReadByte(ace)==0x11) return Rid(IntPtr.Add(ace,8));
    }
    return 0x2000;
   } finally { if(descriptor!=IntPtr.Zero) LocalFree(descriptor); }
  }
  public static int ProcessLevel(int pid) {
   IntPtr process=OpenProcess(0x1000,false,pid); if(process==IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
   IntPtr token=IntPtr.Zero,buffer=IntPtr.Zero;
   try {
    if(!OpenProcessToken(process,8,out token)) throw new Win32Exception(Marshal.GetLastWin32Error());
    uint needed; GetTokenInformation(token,25,IntPtr.Zero,0,out needed);
    buffer=Marshal.AllocHGlobal((int)needed);
    if(!GetTokenInformation(token,25,buffer,needed,out needed)) throw new Win32Exception(Marshal.GetLastWin32Error());
    return Rid(Marshal.ReadIntPtr(buffer));
   } finally { if(buffer!=IntPtr.Zero) Marshal.FreeHGlobal(buffer); if(token!=IntPtr.Zero) CloseHandle(token); CloseHandle(process); }
  }
 }
}
'@
}
