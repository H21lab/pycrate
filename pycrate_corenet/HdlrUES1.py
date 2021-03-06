# -*- coding: UTF-8 -*-
#/**
# * Software Name : pycrate
# * Version : 0.2
# *
# * Copyright 2017. Benoit Michau. ANSSI.
# *
# * This program is free software; you can redistribute it and/or
# * modify it under the terms of the GNU General Public License
# * as published by the Free Software Foundation; either version 2
# * of the License, or (at your option) any later version.
# * 
# * This program is distributed in the hope that it will be useful,
# * but WITHOUT ANY WARRANTY; without even the implied warranty of
# * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# * GNU General Public License for more details.
# * 
# * You should have received a copy of the GNU General Public License
# * along with this program; if not, write to the Free Software
# * Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# * 02110-1301, USA.
# *
# *--------------------------------------------------------
# * File Name : pycrate_corenet/HdlrUES1.py
# * Created : 2017-07-11
# * Authors : Benoit Michau 
# *--------------------------------------------------------
#*/

from .utils      import *
from .ProcCNS1ap import *
from .ProcCNEMM  import *
from .ProcCNESM  import *
from .HdlrUESMS  import *


# WNG: all procedures that call .require_smc() method need to be set in this LUT
ProcAbbrLUT = {
    'EMMAttach'                : 'ATT',
    'EMMTrackingAreaUpdate'    : 'TAU',
    'EMMDetachUE'              : 'DET',
    'EMMServiceRequest'        : 'SER',
    'EMMExtServiceRequest'     : 'SER',
    'EMMCPServiceRequest'      : 'SER',
    }


class UEEMMd(SigStack):
    """UE EMM handler within a UES1d instance
    responsible for EPS Mobility Management signalling procedures
    """
    
    TRACK_PROC = True
    
    # reference to the UEd
    UE = None
    # reference to the UES1d
    S1 = None
    
    # state: INACTIVE (cannot be paged) <-> ACTIVE <-> IDLE
    state = 'INACTIVE'
    
    # to bypass the process() server loop with a custom NAS PDU handler
    RX_HOOK = None
    
    # additional time for letting background task happen in priority
    _WAIT_ADD = 0.005
    
    # list of EMM message types that do not require NAS security to be
    # activated to be processed
    SEC_NOTNEED = {'EMMAttachRequest',
                   'EMMIdentityResponse', # only for IMSI
                   'EMMAuthenticationResponse',
                   'EMMAuthenticationFailure',
                   'EMMSecurityModeReject',
                   'EMMDetachRequestMO', # if sent before security activation
                   'EMMDetachAccept',
                   'EMMTrackingAreaUpdateRequest',
                   'EMMServiceRequest',
                   'EMMExtServiceRequest'
                   }
    
    #--------------------------------------------------------------------------#
    # EMM common parameters
    #--------------------------------------------------------------------------#
    # T3412, periodic TAU timer: dict {'Unit': uint3, 'Value': uint5}
    # Unit: 0: 2s, 1: 1mn, 2: 6mn, 7: deactivated
    #_T3412             = {'Unit': 2, 'Value': 5} # 30mn
    _T3412             = {'Unit': 1, 'Value': 1} # deactivated
    # 
    # Reattach attempt after a failure timer: dict {'Unit': uint3, 'Value': uint5}
    # Unit: 0: 2s, 1: 1mn, 2: 6mn, 7: deactivated
    _T3402              = {'Unit': 1, 'Value': 2} # 2mn
    #
    # T3412Ext, power saving mode, TAU extended timer: None or dict {'Unit': uint3, 'Value': uint5}
    # Unit: 0: 10mn, 1: 1h, 2: 10h, 3: 2s, 4: 30s, 5: 1mn, 6: 320h, 7: deactivated
    _T3412_EXT          = None
    #
    # T3324, power saving mode, time the UE stays active after idle mode following
    # Attach or TAU: None or dict {'Unit': uint3, 'Value': uint5}
    # # Unit: 0: 2s, 1: 1mxn, 2: 6mn, 7: deactivated
    _T3324              = None
    #
    # EPS Network features support: if None, not sent, otherwise dict
    # {'CP_CIoT': uint1, 'ERwoPDN': uint1, 'ESR_PS': uint1, 'CS_LCS': uint2,
    #  'EPC_LCS': uint1, 'EMC_BS': uint1, 'IMS_VoPS': uint1, 'EPCO': uint1,
    #  'HC_CP_CIoT': uint1, 'S1U_Data': uint1, 'UP_CIoT': uint1}
    _EPS_NETFEAT_SUPP   = None
    #
    # Extended DRX support: if None and sent by the UE, value returned it the one
    # from the UE ; otherwise dict {'PTX': uint4, 'eDRX': uint4}
    _EXTDRX             = None
    #
    # SMS service status: if defined (status cause 0 to 3), denies SMS service 
    # for EPS-only attach
    _SMS_SERV_STAT      = None
    
    #--------------------------------------------------------------------------#
    # EMMStatus policy
    #--------------------------------------------------------------------------#
    # behaviour when receiving EMM STATUS
    # 0: do nothing,
    # 1: abort the top-level EMM procedure,
    # 2: abort the whole stack of EMM procedures
    STAT_CLEAR          = 2
    
    #--------------------------------------------------------------------------#
    # EMMGUTIReallocation policy
    #--------------------------------------------------------------------------#
    # EMM procedure timer
    T3450               = 4
    
    #--------------------------------------------------------------------------#
    # EMMAuthentication policy
    #--------------------------------------------------------------------------#
    # this will systematically bypass all authentication procedures
    AUTH_DISABLED       = False
    # EMM procedure timer for auth and smc
    T3460               = 4
    # Authentication Management Field
    AUTH_AMF            = b'\x80\0'
    # if AUTH_PLMN is not None, it will be used for building the 4G auth vector
    # otherwise the main Corenet PLMN will be used
    AUTH_PLMN           = None
    # this is to force a 2G or 3G authentication instead of a 4G one
    AUTH_2G             = False
    AUTH_3G             = False
    # this is to extend AUTN with arbitrary data
    AUTH_AUTN_EXT       = None
    #
    # re-authentication policy:
    # this forces an auth procedure every X EMM TAU / (Ext/CP) SER procedures
    # even if a valid KSI is provided by the UE
    AUTH_TAU            = 1
    AUTH_SER            = 5
    AUTH_DET            = 1 # only applied to Detach without UE power off
    
    #--------------------------------------------------------------------------#
    # EMMSecurityModeControl policy
    #--------------------------------------------------------------------------#
    # this will systematically bypass all NAS SMC procedures during UE signalling
    SMC_DISABLED        = False
    # this will bypass the NAS SMC procedure into specific UE signalling procedure
    # set proc abbreviation in the list: 'ATT', 'TAU', 'SER'
    SMC_DISABLED_PROC   = []
    # list of algorithm priorities
    #SMC_EEA_PRIO        = [2, 1, 0]
    SMC_EEA_PRIO        = [0]
    SMC_EIA_PRIO        = [2, 1]
    #
    # UE security capabilities: add dummy 3G sec cap if GPRS sec cap available
    SMC_SECCAP_W2G      = True
    # UE default algorithm identifier, when everything else is failing...
    SMC_EEA_DEF         = 0
    SMC_EIA_DEF         = 1
    # request IMEISV during a NAS SMC when IMEISV is unknown
    SMC_IMEISV_REQ      = True
    #
    # dummy security cap / context when security is disabled
    SMC_DUMMY_SECCAP    = NAS.UESecCap(val={'EEA0':1, 'EEA1_128':1, 'EEA2_128':1,
                                            'EIA0':1, 'EIA1_128':1, 'EIA2_128':1}).to_bytes()[:2]
    SMC_DUMMY_CTX       = {'Kasme':32*b'\0', 'UL':0, 'DL':0,'UEA':0, 'UIA':0, 'CTX':4}
    
    #--------------------------------------------------------------------------#
    # EMMIdentification policy
    #--------------------------------------------------------------------------#
    # EMM procedure timer
    T3470               = 2
    #
    # potential reject causes:
    # 2: 'IMSI unknown in HLR', -> kill the cellular connectivity until SIM card is removed
    # 3: 'Illegal MS', -> maybe same as 2
    # 4: 'IMSI unknown in VLR',
    # 5: 'IMEI not accepted', -> maybe same as 2
    # 6: 'Illegal ME',
    # 11: 'PLMN not allowed',
    # 12: 'Location Area not allowed',
    # 13: 'Roaming not allowed in this location area',
    # 15: 'No Suitable Cells In Location Area',
    # 17: 'Network failure',
    # 22: 'Congestion'
    # ...
    IDENT_IMSI_NOT_ALLOWED = 11
    IDENT_IMEI_NOT_ALLOWED = 5
    
    #--------------------------------------------------------------------------#
    # EMMAttach policy
    #--------------------------------------------------------------------------#
    ATT_T3412           = _T3412
    ATT_T3402           = _T3402
    ATT_T3412_EXT       = _T3412_EXT
    ATT_T3324           = _T3324
    ATT_EPS_NETFEAT_SUPP = _EPS_NETFEAT_SUPP
    ATT_EXTDRX          = _EXTDRX
    ATT_SMS_SERV_STAT   = _SMS_SERV_STAT
    # if 0, enable IMSI attach from EPS; if > 0, use it as error code
    # e.g. 18: CS domain not available
    ATT_IMSI            = 0
    # if 0, enable emergency attach; if > 0, use it as error code 
    # e.g. 8: EPS services and non-EPS services not allowed)
    ATT_EMERG           = 0
    # if we want to run a GUTI Reallocation within the EMM Attach Accept
    ATT_GUTI_REALLOC    = True
    # if we want to release the S1 ue context after the procedure ends 
    ATT_S1REL           = False
    #
    # when a UEd with MTMSI was created, that in fact corresponds to a UE
    # already set in Server.UE, we need to reject it after updating Server.MTMSI
    ATT_IMSI_PROV_REJECT = 17
    # timer within Attach Reject, Unit: 0: 2s, 1: 1mn, 2: 6mn, 7: deactivated
    ATT_T3346           = {'Unit': 0, 'Value': 2}
    
    #--------------------------------------------------------------------------#
    # EMMTrackingAreaUpdate policy
    #--------------------------------------------------------------------------#
    TAU_T3412           = _T3412
    TAU_T3402           = _T3402
    TAU_T3412_EXT       = _T3412_EXT
    TAU_T3324           = _T3324
    TAU_EPS_NETFEAT_SUPP = _EPS_NETFEAT_SUPP
    TAU_EXTDRX          = _EXTDRX
    TAU_SMS_SERV_STAT   = _SMS_SERV_STAT
    # if we want to run a GUTI Reallocation within the EMM TAU Accept
    TAU_GUTI_REALLOC    = True
    # if we want to release the S1 ue context after the procedure ends 
    TAU_S1REL           = False
    
    #--------------------------------------------------------------------------#
    # EMMServiceRequest policy
    #--------------------------------------------------------------------------#
    # to always start an SMC after a service request, even if no auth happened
    SER_SMC_ALW         = False
    
    
    def _log(self, logtype, msg):
        self.S1._log(logtype, '[EMM] %s' % msg)
    
    def __init__(self, ued, ues1d):
        self.UE = ued
        self.set_s1(ues1d)
        #
        # ready event, used by foreground tasks (network / interpreter initiated)
        self.ready = Event()
        self.ready.set()
        # stack of ongoing EMM procedures (i.e. common procedures can be run 
        # within specific procedure)
        self.Proc   = []
        # list of tracked procedures (requires TRACK_PROC = True)
        self._proc  = []
    
    def set_s1(self, ues1d):
        self.S1 = ues1d
    
    def process(self, NasRx):
        """process a NAS EMM message (NasRx) sent by the UE,
        and return a list (possibly empty) of S1AP procedure(s) to be sent back 
        to the eNB
        
        NasRx has 2 additional attributes (_sec [bool], _ulcnt [uint])
        """
        if self.RX_HOOK is not None:
            return self.RX_HOOK(NasRx)
        #
        name = NasRx._name
        # 1) in case sec check failed, see if request is still to be accepted
        if not NasRx._sec and name not in self.SEC_NOTNEED:
            # discard the msg
            self._log('INF', 'discarding %s message, failed security check' % name)
            return []
        #
        # 2) check if it is a Detach Request
        if name == 'EMMDetachRequestMO':
            Proc = EMMDetachUE(self)
            self.Proc.append( Proc )
            if self.TRACK_PROC:
                self._proc.append(Proc)
            # GMMDetachUE.process() will abort every other ongoing NAS procedures
            # for the PS domain
            return Proc.process(NasRx)
        #
        # 3) check if there is any ongoing EMM procedure
        elif self.Proc:
            # 2.1) in case of STATUS, disable ongoing procedure(s)
            if name == 'EMMStatus':
                self._log('WNG', 'STATUS received with %r' % NasRx['EMMCause'])
                if self.STAT_CLEAR == 1:
                    #self._log('WNG', 'STATUS, disabling %r' % self.Proc[-1])
                    self.Proc[-1].abort()
                elif self.STAT_CLEAR == 2:
                    #self._log('WNG', 'STATUS, disabling %r' % self.Proc)
                    self.clear()
                return []
            #
            # 2.2) in case of expected response
            elif name in self.Proc[-1].FilterStr:
                Proc = self.Proc[-1]
                S1apTxProc = Proc.process(NasRx)
                while self.Proc and not S1apTxProc:
                    # while the top-level NAS procedure has nothing to respond and terminates,
                    # we postprocess() lower-level NAS procedure(s) until we have something
                    # to send, or the stack is empty
                    ProcLower = self.Proc[-1]
                    S1apTxProc = ProcLower.postprocess(Proc)
                    Proc = ProcLower
                return S1apTxProc
            #
            # 2.3) in case of unexpected NasRx
            else:
                self._log('WNG', 'unexpected %s message, sending STATUS 98' % name)
                # cause 98: Message type not compatible with the protocol state
                return self.S1.ret_s1ap_dnt(NAS.EMMStatus(val={'EMMCause':98}, sec=NasRx._sec))
        #
        # 3) start a new UE-initiated procedure
        elif name in EMMProcUeDispatcherStr:
            Proc = EMMProcUeDispatcherStr[name](self)
            self.Proc.append( Proc )
            if self.TRACK_PROC:
                self._proc.append(Proc)
            return Proc.process(NasRx)
        #
        # 4) unexpected NasRx
        else:
            self._log('WNG', 'unexpected %s message, sending STATUS 96' % name)
            # cause 96: Invalid mandatory information
            return self.S1.ret_s1ap_dnt(NAS.EMMStatus(val={'EMMCause':96}, sec=NasRx._sec))
    
    def init_proc(self, ProcClass, encod=None, emm_preempt=False, **kw):
        """initialize a CN-initiated EMM procedure of class `ProcClass' and 
        given encoder(s), and return the procedure
        """
        Proc = ProcClass(self, encod=encod, emm_preempt=emm_preempt, **kw)
        self.Proc.append( Proc )
        if self.TRACK_PROC:
            self._proc.append( Proc )
        return Proc
    
    def clear(self):
        """abort all running procedures
        """
        for Proc in self.Proc[::-1]:
            Proc.abort()
    
    #--------------------------------------------------------------------------#
    # SMC and security-related methods
    #--------------------------------------------------------------------------#
    
    def require_auth(self, Proc, ksi=None):
        # ksi is a 2-tuple (TSC 0..1, Value 0..7)
        # check if an EMMAuthentication procedure is required
        if self.S1.SECNAS_DISABLED or self.AUTH_DISABLED:
            return False
        elif ksi is None or ksi[1] == 7:
            self.S1.SEC['KSI'] = None
            return True
        #
        ksi = (ksi[0]<<3) + ksi[1]
        if ksi not in self.S1.SEC:
            self.S1.SEC['KSI'] = None
            return True
        #
        else:
            # auth policy per EMM procedure
            if isinstance(Proc, EMMAttach):
                # always authenticate within an Attach
                return True
            elif isinstance(Proc, EMMTrackingAreaUpdate):
                self.S1.SEC['POL']['TAU'] += 1
                if self.AUTH_TAU and self.S1.SEC['POL']['TAU'] % self.AUTH_TAU == 0:
                    self.S1.SEC['KSI'] = None
                    return True
                else:
                    self.S1.SEC['KSI'] = ksi
                    return False
            elif isinstance(Proc, EMMDetachUE):
                self.S1.SEC['POL']['DET'] += 1
                if self.AUTH_DET and self.S1.SEC['POL']['TAU'] % self.AUTH_DET == 0:
                    self.S1.SEC['KSI'] = None
                    return True
                else:
                    self.S1.SEC['KSI'] = ksi
                    return False
            elif isinstance(Proc, (EMMServiceRequest, EMMExtServiceRequest, EMMCPServiceRequest)):
                self.S1.SEC['POL']['SER'] += 1
                if self.AUTH_SER and self.S1.SEC['POL']['SER'] % self.AUTH_SER == 0:
                    self.S1.SEC['KSI'] = None
                    return True
                else:
                    self.S1.SEC['KSI'] = ksi
                    return False
            else:
                # auth not required, use the UE-provided cksn in use
                self.S1.SEC['KSI'] = ksi
                return False
    
    def require_smc(self, Proc):
        # check if an EMMSecurityModeControl procedure is required
        if self.S1.SECNAS_DISABLED or self.SMC_DISABLED:
            return False
        #
        elif ProcAbbrLUT[Proc.Name] in self.SMC_DISABLED_PROC:
            return False
        #
        elif self.S1.SEC['KSI'] is None or self.S1.SEC['KSI'] not in self.S1.SEC:
            # no security context established, cannot run an smc
            self._log('WNG', 'require_smc: no KSI set, unable to run an SMC')
            return False
        #
        else:
            return True
    
    def get_any_ksi(self):
        cur = self.S1.SEC['KSI']
        if cur is not None:
            if cur in self.S1.SEC:
                return cur
            else:
                self.S1.SEC['KSI'] = None
        #
        for i in range(0, 7):
            if i in self.S1.SEC:
                self.S1.SEC['KSI'] = i
                return i
        for i in range(8, 15):
            if i in self.S1.SEC:
                self._log('INF', 'selecting a mapped KSI %i' % i)
                self.S1.SEC['KSI'] = i
                return i
        return None
    
    def get_new_ksi(self):
        for i in range(0, 7):
            if i not in self.S1.SEC:
                return i
        # all native KSI have been used, clear all of them except the current one
        # if defined
        cur = self.S1.SEC['KSI']
        for i in range(0, 7):
            if i != cur:
                del self.S1.SEC[i]
        if cur == 0:
            return 1
        else:
            return 0
    
    def set_sec_ctx(self, ksi, ctx, vect):
        ksi = (ksi[0]<<3) + ksi[1]
        if ctx == 3:
            if self.AUTH_PLMN:
                snid = plmn_str_to_buf(self.AUTH_PLMN)
            else:
                snid = plmn_str_to_buf(self.UE.PLMN)
            Kasme  = conv_A2(vect[3], vect[4], snid, vect[2][:6])
            secctx = {'VEC'  : vect,
                      'CTX'  : ctx,
                      'CK'   : vect[3],
                      'IK'   : vect[4],
                      'Kasme': Kasme}
        elif ctx == 2:
            # WNG: this is undefined / illegal and won't work (hopefully)
            CK, IK = conv_C4(vect[2]), conv_C5(vect[2])
            if self.AUTH_PLMN:
                snid = plmn_str_to_buf(self.AUTH_PLMN)
            else:
                snid = plmn_str_to_buf(self.UE.PLMN)
            Kasme  = conv_A2(CK, IK, snid, b'\0\0\0\0\0\0')
            secctx = {'VEC'  : vect,
                      'CTX'  : ctx,
                      'Kc'   : vect[2],
                      'CK'   : CK,
                      'IK'   : IK,
                      'Kasme': Kasme}
        else:
            # ctx == 4
            secctx = {'VEC'  : vect,
                      'CTX'  : ctx,
                      'Kasme': vect[3]}
        #
        secctx['UL'], secctx['DL'], secctx['UL_enb'] = 0, 0, 0
        self.S1.SEC[ksi] = secctx
        self.S1.SEC['KSI'] = ksi
    
    def set_sec_ctx_emerg(self):
        secctx = {'CTX'    : 0,
                  'Kasme'  : 32*b'\0',
                  'Knasenc': 16*b'\0',
                  'Knasint': 16*b'\0',
                  'EEA'    : 0,
                  'EIA'    : 0,
                  'UL'     : 0,
                  'DL'     : 0,
                  'UL_enb' : 0}
        self.S1.SEC[0] = secctx
    
    def set_sec_ctx_smc(self, ksi):
        try:
            secctx = self.S1.SEC[ksi]
        except:
            pass
        else:
            secctx['EEA'], secctx['EIA'] = self._get_sec_eea(), self._get_sec_eia()
            secctx['Knasenc'] = conv_A7(secctx['Kasme'], 1, secctx['EEA'])[16:32]
            secctx['Knasint'] = conv_A7(secctx['Kasme'], 2, secctx['EIA'])[16:32]
    
    def set_sec_cap(self):
        # build UESecCap from UENetCap
        if 'UENetCap' in self.UE.Cap:
            ueseccap = self.UE.Cap['UENetCap'][0]
            if len(ueseccap) > 4:
                # we have more than 3G and 4G sec cap
                ueseccap = ueseccap[:4]
            if len(ueseccap) == 4:
                # void UCS2 support
                lastoct  = ord(ueseccap[3:4])
                if lastoct & 0x80:
                    ueseccap = ueseccap[:3] + bchr(lastoct^0x80)
                if 'MSNetCap' in self.UE.Cap:
                    ueseccap += self._get_sec_gea_cap()
            else:
                assert( len(ueseccap) == 2 )
                if self.SMC_SECCAP_W2G and 'MSNetCap' in self.UE.Cap:
                    ueseccap += b'\0\0'
                    ueseccap += self._get_sec_gea_cap()
            UESecCap = NAS.UESecCap()
            UESecCap.from_bytes(ueseccap)
            self.UE.Cap['UESecCap'] = (ueseccap, UESecCap)
    
    def _get_sec_gea_cap(self):
        msnetcap = self.UE.Cap['MSNetCap'][1]()
        v = msnetcap[0]<< 6 # GEA1
        if isinstance(msnetcap[8], list):
            # Extended_GEA_bits
            for i, b in enumerate(msnetcap[8]):
                v += b << (5-i)
        # TODO: add GIA sec cap
        return bchr(v)
    
    def get_sec_cap(self):
        if 'UESecCap' not in self.UE.Cap:
            # build UESecCap from UENetCap
            if 'UENetCap' in self.UE.Cap:
                self.set_sec_cap()
                return self.UE.Cap['UESecCap'][0]
            else:
                # build UESecCap from SMC_DUMMY_SECCAP
                self._log('WNG', 'no security capabilities available, using dummy ones')
                return self.SMC_DUMMY_SECCAP
        else:
            return self.UE.Cap['UESecCap'][0]
    
    def _get_sec_eea(self):
        if 'UESecCap' not in self.UE.Cap:
            self._log('WNG', 'no security capabilities available, using EEA%i' % self.SMC_EEA_DEF)
            return self.SMC_EEA_DEF
        else:
            UESecCap = self.UE.Cap['UESecCap'][1]
            for eea in self.SMC_EEA_PRIO:
                if UESecCap._content[eea].get_val():
                    return eea
            self._log('INF', 'no matching EEA identifier, using EEA%i' % self.SMC_EEA_DEF)
            return self.SMC_EEA_DEF
    
    def _get_sec_eia(self):
        if 'UESecCap' not in self.UE.Cap:
            self._log('WNG', 'no security capabilities available, using EIA%i' % self.SMC_EIA_DEF)
            return self.SMC_EIA_DEF
        else:
            UESecCap = self.UE.Cap['UESecCap'][1]
            for eia in self.SMC_EIA_PRIO:
                if UESecCap._content[8+eia].get_val():
                    return eia
            self._log('INF', 'no matching EIA identifier, using EIA%i' % self.SMC_EIA_DEF)
            return self.SMC_EEA_DEF
    
    #--------------------------------------------------------------------------#
    # network-initiated method (fg task, to be used from the interpreter)
    #--------------------------------------------------------------------------#
    
    def _net_init_con(self):
        if not self.S1.page_block():
            return False
        # need to wait for potential EMM serving / common procedures to happen and end
        sleep(self._WAIT_ADD)
        if not self.ready.wait(10):
            # something is blocking in the serving / common procedures
            return False
        elif not self.S1.connected.is_set():
            # something went wrong during the serving / common procedures
            return False
        else:
            return True


class UEESMd(SigStack):
    """UE ESM handler within a UES1d instance
    responsible for EPS Session Management signalling procedures
    """
    
    TRACK_PROC = True
    
    # reference to the UEd
    UE = None
    # reference to the UES1d
    S1 = None
    
    # to bypass the process() server loop with a custom NAS PDU handler
    RX_HOOK = None
    
    # list of ESM message types that do not require NAS security to be
    # activated to be processed
    SEC_NOTNEED = {'ESMPDNConnectivityRequest'}
    
    # default Radio Access Bearer settings for PDN config, per APN
    # QCI (being LTE + EPS) is copied from the CorenetServer.ConfigPDN at UE init
    RABConfig = {
        '*'      : {'PriorityLevel': 15, # no priority
                    'PreemptCap'   : 'shall-not-trigger-pre-emption', # or 'may-trigger-pre-emption'
                    'PreemptVuln'  : 'not-pre-emptable', # or 'pre-emptable'
                    'BitrateDL'    : 100000000, # aggregate max bitrate downlink (b/s)
                    'BitrateUL'    : 50000000, # aggregate max bitrate uplink (b/s)
                    },
        'corenet': {'PriorityLevel': 15, # no priority
                    'PreemptCap'   : 'shall-not-trigger-pre-emption', # or 'may-trigger-pre-emption'
                    'PreemptVuln'  : 'not-pre-emptable', # 'pre-emptable'
                    'BitrateDL'    : 100000000, # aggregate max bitrate downlink (b/s)
                    'BitrateUL'    : 50000000, # aggregate max bitrate uplink (b/s)
                    }
        }
    # when the UE 1st attach it gets a specific PDNConfig dict with a copy of this content
    # under the key 'RAB'
    
    # Protocol config option with authentication
    # if bypass enabled, the PAP / CHAP authentication will not be checked against
    # the CorenetServer.PDNConfig and always return authentication success
    PDN_PAP_BYPASS  = True
    PDN_CHAP_BYPASS = True
    
    #--------------------------------------------------------------------------#
    # ESMStatus policy
    #--------------------------------------------------------------------------#
    # behaviour when receiving ESM STATUS
    # 0: do nothing,
    # 1: abort the top ESM procedure for the indicated EPS bearer ID
    # 2: abort the whole ESM procedure stack for the indicated EPS bearer ID
    # 3: abort all the ESM procedures stacks
    STAT_CLEAR = 3
    
    #--------------------------------------------------------------------------#
    # ESMDefaultEPSBearerCtxtAct / ESMDedicatedEPSBearerCtxtAct policy
    #--------------------------------------------------------------------------#
    T3485 = 2
    
    #--------------------------------------------------------------------------#
    # ESMEPSBearerCtxtModif policy
    #--------------------------------------------------------------------------#
    T3486 = 2
    
    #--------------------------------------------------------------------------#
    # ESMEPSBearerCtxtDeact policy
    #--------------------------------------------------------------------------#
    T3495 = 2
    
    #--------------------------------------------------------------------------#
    # ESMInfoRequest policy
    #--------------------------------------------------------------------------#
    T3489 = 2
    
    
    def _log(self, logtype, msg):
        self.S1._log(logtype, '[ESM] %s' % msg)
    
    def __init__(self, ued, ues1d):
        self.UE = ued
        self.set_s1(ues1d)
        #
        # dict of ongoing ESM procedures (indexed by EPS bearer ID)
        self.Proc  = {i: [] for i in range(16)}
        # dict of configured PDN, indexed by EPS bearer ID
        self.PDN   = {}
        # dict of ongoing ESM transactions IEs
        self.Trans = {}
        # list of tracked procedures (requires TRACK_PROC = True)
        self._proc = []
    
    def set_s1(self, ues1d):
        self.S1 = ues1d
    
    def process_buf(self, buf, sec, EMMProc=None):
        """process a NAS ESM message buffer (buf) sent by the UE,
        if the decoding is correct, return the result of process()
        """
        ESMRx, err = NAS.parse_NASLTE_MO(buf, inner=False)
        if err:
            # invalid ESM message
            self._log('WNG', 'invalid EPS NAS ESM message: %s' % hexlify(buf).decode('ascii'))
            ESMTx = NAS.ESMStatus(val={'ESMCause':err}, sec=sec)
            return self.S1.ret_s1ap_dnt(self.output_nas_esm(ESMTx, EMMProc))
        #
        elif ESMRx['ProtDisc'].get_val() != 2:
            # cause 96: Invalid mandatory information
            self._log('WNG', 'invalid EPS NAS ESM message: %r' % ESMRx)
            ESMTx = NAS.ESMStatus(val={'ESMCause':96}, sec=sec)
            return self.S1.ret_s1ap_dnt(self.output_nas_esm(ESMTx, EMMProc))
        #
        elif self.UE.TRACE_NAS_EPS:
            self._log('TRACE_NAS_EPS_UL', '\n' + ESMRx.show())
        ESMRx._sec = sec
        return self.process(ESMRx, EMMProc=EMMProc)
    
    def process(self, NasRx, EMMProc=None):
        """process a NAS ESM message (NasRx) sent by the UE,
        and return a list (possibly empty) of S1AP procedure(s) to be sent back 
        to the eNB
        
        NasRx has 2 additional attributes (_sec [bool], _ulcnt [uint])
        
        EMMProc [EMMSigProc or None], indicates if the NAS ESM message is handled in 
        the context of an EMM procedure 
        """
        if self.RX_HOOK is not None:
            return self.RX_HOOK(NasRx)
        #
        name = NasRx._name
        # 1) in case sec check failed, see if request is still to be accepted
        if not NasRx._sec and name not in self.SEC_NOTNEED:
            # discard the msg
            self._log('INF', 'discarding %s message, failed security check' % name)
            return self.S1.ret_s1ap_dnt(self.output_nas_esm(None, EMMProc))
        #
        # 2) check if there is any ongoing ESM procedure for the given EPS bearer id
        ebi = NasRx[0].get_val()
        if self.Proc[ebi]:
            ProcStack = self.Proc[ebi]
            # 2.1) in case of STATUS, disable ongoing procedure(s)
            if name == 'ESMStatus':
                self._log('WNG', 'STATUS received with %r' % NasRx['ESMCause'])
                if self.STAT_CLEAR == 1:
                    #self._log('WNG', 'STATUS, disabling %r' % ProcStack[-1])
                    ProcStack[-1].abort()
                elif self.STAT_CLEAR == 2:
                    #self._log('WNG', 'STATUS, disabling %r' % ProcStack)
                    self.clear(ebi)
                elif self.STAT_CLEAR == 3:
                    #self._log('WNG', 'STATUS, disabling %r' % self.Proc)
                    self.clear()
                return self.S1.ret_s1ap_dnt(self.output_nas_esm(None, EMMProc))
            #
            # 2.2) in case of expected response
            elif name in ProcStack[-1].FilterStr:
                Proc = ProcStack[-1]
                S1apTxProc = Proc.process(NasRx)
                while ProcStack and not S1apTxProc:
                    # while the top-level NAS procedure has nothing to respond and terminates,
                    # we postprocess() lower-level NAS procedure(s) until we have something
                    # to send, or the stack is empty
                    ProcLower = ProcStack[-1]
                    S1apTxProc = ProcLower.postprocess(Proc)
                    Proc = ProcLower
                return S1apTxProc
            #
            # 2.3) in case of unexpected NasRx
            else:
                self._log('WNG', 'unexpected %s message, sending STATUS 98' % name)
                # cause 98: Message type not compatible with the protocol state
                ESMTx = NAS.ESMStatus(val={'ESMCause':96}, sec=NasRx._sec)
                return self.S1.ret_s1ap_dnt(self.output_nas_esm(ESMTx, EMMProc))
        #
        # 3) start a new UE-initiated procedure
        elif name in ESMProcUeDispatcherStr:
            Proc = ESMProcUeDispatcherStr[name](self, ebi=ebi, EMMProc=EMMProc)
            self.Proc[ebi].append(Proc)
            if self.TRACK_PROC:
                self._proc.append(Proc)
            return Proc.process(NasRx)
        #
        # 4) unexpected NasRx
        else:
            self._log('WNG', 'unexpected %s message, sending STATUS 96' % name)
            # cause 96: Invalid mandatory information
            ESMTx = NAS.ESMStatus(val={'ESMCause':96}, sec=NasRx._sec)
            return self.S1.ret_s1ap_dnt(self.output_nas_esm(ESMTx, EMMProc))
    
    def output_nas_esm(self, ESMTx, EMMProc):
        if not ESMTx:
            if EMMProc:
                self._log('WNG', 'no ESMTx but an EMMTx to be sent')
                return EMMProc._nas_tx
            else:
                return None
        elif EMMProc:
            ESMTx._sec = False
            EMMTx = EMMProc._nas_tx
            EMMTx['ESMContainer']['V'].set_val(self.S1.output_nas_sec(ESMTx))
            return EMMTx
        else:
            return ESMTx
    
    def init_proc(self, ProcClass, **kw):
        """initialize a CN-initiated ESM procedure of class `ProcClass' and 
        given encoder(s), and return the procedure
        """
        if 'ebi' in kw:
            ebi = kw['ebi']
            assert( 0 <= ebi <= 15 )
            del kw['ebi']
        else:
            ebi = 0
        Proc = ProcClass(self, ebi=ebi, **kw)
        self.Proc[ebi].append( Proc )
        if self.TRACK_PROC:
            self._proc.append( Proc )
        return Proc
    
    def clear(self, ebi=None):
        """abort all running procedures, eventually for a single EPS Bearer ID
        """
        self.Trans.clear()
        if ebi is None:
            for ebi in range(16):
                for Proc in self.Proc[ebi][::-1]:
                    Proc.abort()
        else:
            for Proc in self.Proc[ebi][::-1]:
                Proc.abort()
    
    def pdn_clear(self, ebi=None):
        if ebi is None:
            for ebi, pdncfg in list(self.PDN.items()):
                self.UE.Server.GTPUd.rem_mobile(pdncfg['RAB']['SGW-GTP-TEID'])
                del self.PDN[ebi]
        elif ebi in self.PDN:
            self.UE.Server.GTPUd.rem_mobile(self.PDN[ebi]['RAB']['SGW-GTP-TEID'])
            del self.PDN[ebi]
    
    def pdn_suspend(self, ebi=None):
        if ebi is None:
            for ebi, pdncfg in self.PDN.items():
                if pdncfg['state'] == 1:
                    self.UE.Server.GTPUd.rem_mobile(pdncfg['RAB']['SGW-GTP-TEID'])
                    pdncfg['state'] = 0
        elif ebi in self.PDN and self.PDN[ebi]['state'] == 1:
            self.UE.Server.GTPUd.rem_mobile(self.PDN[ebi]['RAB']['SGW-GTP-TEID'])
            self.PDN[ebi]['state'] = 0
    
    #--------------------------------------------------------------------------#
    # transaction processing
    #--------------------------------------------------------------------------#
    
    def process_trans(self, trans_id):
        """process an ESM transaction initiated by the UE, and return a network-initiated
        procedure with IEs configured and None, or None and the ESM error code
        """
        try:
            trans = self.Trans[trans_id]
        except:
            # err cause 47: PTI mismatch
            return None, 47
        #
        if trans['Type'] == 'Default':
            IEs = {}
            #
            # 1) need APN
            if trans['APN'] is None:
                # err cause 27: missing APN
                return None, 27
            apn = trans['APN'][0][1].get_val()
            if apn in self.PDNConfig:
                pdncfg = self.PDNConfig[apn]
            elif '*' in self.PDNConfig:
                pdncfg = self.PDNConfig['*']
            else:
                # err cause 27: unknown APN
                return None, 27
            IEs['APN'] = trans['APN'].get_val()
            #
            # 2) check the ue request against pdncfg
            # 2.1) check the PDN type
            pdntue  = trans['PDNType'].get_val()
            pdntnet = pdncfg['PDNAddr'][0]
            if pdntue == 1:
                if pdntnet not in (1, 3):
                    # err cause 51: PDN type IPv6 only allowed
                    return None, 51
                else:
                    IEs['PDNAddr'] = {'Type': 1, 'Addr': inet_aton_cn(1, pdncfg['PDNAddr'][1])}
                    ipaddr = (1, pdncfg['PDNAddr'][1])
            elif pdntue == 2:
                if pdntnet not in (2, 3):
                    # err cause 50: PDN type IPv4 only allowed
                    return None, 50
                else:
                    IEs['PDNAddr'] = {'Type': 2, 'Addr': inet_aton_cn(2, pdncfg['PDNAddr'][2])}
                    ipaddr = (2, pdncfg['PDNAddr'][2])
            elif pdntue == 3:
                if not 1 <= pdntnet <= 3:
                    # err cause 111: Protocol error, unspecified
                    return None, 111
                else:
                    IEs['PDNAddr'] = {'Type': pdntnet, 'Addr': inet_aton_cn(*pdncfg['PDNAddr'])}
                    ipaddr = pdncfg['PDNAddr']
            else:
                # err cause 28: Unknown PDN type
                return None, 28
            #
            # 2.2) check the protocol config options
            if trans['ProtConfig']:
                IEs['ProtConfig'], pdnaddrreq = self.process_protconfig(pdncfg, trans['ProtConfig'])
                if not pdnaddrreq:
                    IEs['PDNAddr'] = b''
            #
            if 'NBIFOMContainer' in trans:
                self._log('WNG', 'NBIFOMContainer IE unsupported')
            if 'HdrCompConfig' in trans:
                self._log('WNG', 'HdrCompConfig IE unsupported')
            if 'ExtProtConfig' in trans:
                self._log('WNG', 'ExtProtConfig IE unsupported')
            #
            # 3) get the default QCI for the given APN
            IEs['EPSQoS'] = {'QCI': pdncfg.get('QCI', 0x80)}
            #
            # 4) get the 1st available EPS bearer ID
            ebi = 0
            for i in range(5, 16):
                if i not in self.PDN:
                    ebi = i
                    break
            if not ebi:
                # err cause 65: Maximum number of EPS bearers reached
                return None, 65
            #
            # 5) set the default RAB for the given APN / EPS bearer ID
            self.rab_set_default(ebi, apn, ipaddr, pdncfg)
            #
            # initialize an ESMDefaultEPSBearerCtxtAct with the given EPS Bearer ID and IEs
            return self.init_proc(ESMDefaultEPSBearerCtxtAct, ebi=ebi, encod={(2, 193): IEs}), None
        
        #
        elif trans['Type'] == 'Dedicated':
            # TODO
            return None, None
        elif trans['Type'] == 'Modif':
            # TODO
            return None, None
        elif trans['Type'] == 'Deact':
            # TODO
            return None, None
        else:
            assert()
    
    def rab_set_default(self, ebi, apn, ipaddr, pdncfg):
        pdn = cpdict(pdncfg)
        pdn['PDNAddr'] = ipaddr
        pdn['APN'] = apn
        pdn['RAB'].update({
            'E-RABlevelQoSParameters': {
                'qCI': pdncfg['QCI'],
                'allocationRetentionPriority': {
                    'priorityLevel': pdncfg['RAB']['PriorityLevel'],
                    'pre-emptionCapability': pdncfg['RAB']['PreemptCap'],
                    'pre-emptionVulnerability': pdncfg['RAB']['PreemptVuln']
                    },
                },
            'SGW-TLA': self.UE.Server.get_sgw_addr(),
            'ENB-TLA': None, # will be updated after eNB setup ERAB
            'SGW-GTP-TEID': self.UE.Server.get_gtp_teid(), # teid_ul
            'ENB-GTP-TEID': None, # teid_dl, will be updated after the eNB setup the ERAB
            })
        pdn['state'] = 0 # 0: suspended (to GTP tunnel exists), 1: active (GTP tunnel exists)
        self.PDN[ebi] = pdn
    
    #--------------------------------------------------------------------------#
    # protocol configuration processing
    #--------------------------------------------------------------------------#
    
    def process_protconfig(self, config, request):
        """process an EPS Protocol Configuration Options request, and returns
        the response Protocol Configuration Options message, and a bool indicating 
        if the PDN address for the UE is required in the NAS signalling
        """
        if request[2].get_val() != 0:
            # not PPP with IP PDP
            return None
        RespElt, pdnaddrreq = [], False
        #self._log('DBG', 'config: %r' % config)
        #self._log('DBG', 'request: %r' % request)
        for ReqElt in request[3]:
            pcid = ReqElt[0].get_val()
            #
            if pcid == 0x8021:
                # IPCP
                if isinstance(ReqElt[2], NAS.NCP) and ReqElt[2][0].get_val() == 1 \
                and isinstance(ReqElt[2][3], NAS.NCPDataConf):
                    # NCP config req
                    ncpreq = []
                    for NcpOpt in ReqElt[2][3]:
                        ncpreq.append( NcpOpt[0].get_val() )
                    NcpOptResp, dnsind = [], 0
                    if 3 in ncpreq:
                        # IPv4 addr
                        ip = None
                        for ipaddr in config['PDNAddr']:
                            if ipaddr[0] == 1:
                                ip = inet_aton_cn(*ipaddr)
                                break
                            elif ipaddr[0] == 3:
                                ip = inet_aton_cn(1, ipaddr[1])
                                break
                        if ip is None:
                            self._log('WNG', 'protocol config element IPCP IPv4 address: '\
                                      'no config available')
                        else:
                            NcpOptResp.append({'Type': 3, 'Data': ip})
                        ncpreq.remove(3)
                    if 129 in ncpreq:
                        # 1st DNS IPv4 addr
                        dns = None
                        if 'DNS' in config:
                            for dnsaddr in config['DNS']:
                                dnsind += 1
                                if dnsaddr[0] == 1:
                                    dns = inet_aton_cn(*dnsaddr)
                                    break
                        if dns is None:
                            self._log('WNG', 'protocol config element IPCP 1st DNS IPv4: '\
                                      'no config available')
                        else:
                            NcpOptResp.append({'Type': 129, 'Data': dns})
                        ncpreq.remove(129)
                    if 131 in ncpreq:
                        # 2nd DNS IPv4 addr
                        dns = None
                        if 'DNS' in config:
                            for dnsaddr in config['DNS'][dnsind:]:
                                if dnsaddr[0] == 1:
                                    dns = inet_aton_cn(*dnsaddr)
                                    break
                        if dns is None:
                            self._log('WNG', 'protocol config element IPCP 2nd DNS IPv4: '\
                                      'no config available')
                        else:
                            NcpOptResp.append({'Type': 131, 'Data': dns})
                        ncpreq.remove(131)
                    if ncpreq:
                        self._log('WNG', 'protocol config element IPCP unsupported, types: %r' % ncpreq)
                    RespElt.append({'ID': 32801,
                                    'Cont':{'Code': 2, 'Id': ReqElt[2][1].get_val(), 'Data': NcpOptResp}})
                else:
                    self._log('WNG', 'protocol config element IPCP invalid: %r' % ReqElt)
            #
            elif pcid == 0xC021:
                # LCP
                if isinstance(ReqElt[2], NAS.LCP) and ReqElt[2][0].get_val() == 1 \
                and isinstance(ReqElt[2][3], NAS.LCPDataConf):
                    # NCP config req
                    lcpreq = []
                    for LcpOpt in ReqElt[2][2]:
                        lcpreq.append( LcpOpt[0].get_val() )
                    if lcpreq:
                        # TODO
                        self._log('ERR', 'protocol config element LCP unsupported: %r' % ReqElt[2])
                else:
                    self._log('WNG', 'protocol config element LCP invalid: %r' % ReqElt)
            #
            elif pcid == 0xC023:
                # PAP
                if isinstance(ReqElt[2], NAS.PAP) and ReqElt[2][0].get_val() == 1:
                    # PAP req
                    if self.PDN_PAP_BYPASS:
                        RespElt.append({'ID': 0xC023,
                                        'Cont': {'Code': 2, # Ack
                                                 'Id': ReqElt[2][1].get_val(),
                                                 'Data':{'Msg': b''}}})
                    
                    else:
                        authreq, ack = ReqElt[2][3], False
                        peerid, passwd = authreq[1].get_val(), authreq[3].get_val()
                        if 'PAP' in config and peerid in config['PAP'] and passwd == config['PAP'][peerid]:
                            RespElt.append({'ID': 0xC023,
                                            'Cont': {'Code': 2, # Ack
                                                     'Id': ReqElt[2][1].get_val(),
                                                     'Data':{'Msg': b''}}})
                        else:
                            if 'PAP' not in config:
                                self._log('WNG', 'protocol config element PAP: no config available')
                            RespElt.append({'ID': 0xC023,
                                            'Cont': {'Code': 3, # Nak
                                                     'Id': ReqElt[2][1].get_val(),
                                                     'Data':{'Msg': b'you loose'}}})
                else:
                    self._log('WNG', 'protocol config element PAP invalid: %r' % ReqElt)
            #
            elif pcid == 0xC223:
                # CHAP
                if isinstance(ReqElt[2], NAS.CHAP) and ReqElt[2][0].get_val() == 1:
                    # CHAP req
                    if self.PDN_CHAP_BYPASS:
                        RespElt.append({'ID': 0xC223,
                                        'Cont': {'Code': 3, # success
                                                 'Id': ReqElt[2][1].get_val(),
                                                 'Data': b''}})
                    else:
                        # TODO
                        self._log('ERR', 'protocol config element CHAP unsupported')
                        RespElt.append({'ID': 0xC223,
                                        'Cont': {'Code': 4, # failure
                                                 'Id': ReqElt[2][1].get_val(),
                                                 'Data': b''}})                    
                else:
                    self._log('WNG', 'protocol config element CHAP invalid: %r' % ReqElt)
            #
            elif pcid == 0x3:
                # DNS IPv6
                dns = None
                if 'DNS' in config:
                    for dnsaddr in config['DNS']:
                        if dnsaddr[0] == 2:
                            dns = inet_aton_cn(*dnsaddr)
                            break
                if dns is None:
                    self._log('WNG', 'protocol config element DNS IPv6: no config available')
                else:
                    RespElt.append({'ID': 0x3, 'Cont': dns})
            elif pcid == 0xA:
                # IP alloc via NAS
                pdnaddrreq = True
            elif pcid == 0xD:
                # DNS IPv4
                dns = None
                if 'DNS' in config:
                    for dnsaddr in config['DNS']:
                        if dnsaddr[0] == 1:
                            dns = inet_aton_cn(*dnsaddr)
                            break
                if dns is None:
                    self._log('WNG', 'protocol config element DNS IPv4: no config available')
                else:
                    RespElt.append({'ID': 0xD, 'Cont': dns})
            elif pcid == 0x10:
                # IPv4 link MTU
                if 'MTU' in config:
                    mtu = config['MTU'][0]
                    if isinstance(mtu, integer_types) and 0 <= mtu <= 65535:
                        mtu = pack('>H', mtu)
                    if isinstance(mtu, bytes_types):
                        RespElt.append({'ID': 0x10, 'Cont': mtu})
                else:
                    self._log('DBG', 'protocol config element IPv4 MTU: no config available')
            elif pcid == 0x15:
                # non-IP link MTU
                if 'MTU' in config:
                    mtu = config['MTU'][1]
                    if isinstance(mtu, integer_types) and 0 <= mtu <= 65535:
                        mtu = pack('>H', mtu)
                    if isinstance(mtu, bytes_types):
                        RespElt.append({'ID': 0x15, 'Cont': mtu})
                else:
                    self._log('DBG', 'protocol config element non-IP MTU: no config available')
            else:
                self._log('WNG', 'protocol config element not supported: %r' % ReqElt)
        #
        return {'Config': RespElt}, pdnaddrreq


class UES1d(SigStack):
    """UE S1 handler within a CorenetServer instance
    responsible for UE-associated S1AP signalling
    """
    
    # to keep track of all S1AP procedures
    TRACK_PROC = True
    
    # domain
    DOM = 'EPS'
    
    # reference to the UEd
    UE  = None
    # reference to the ENBd, SCTP stream id
    ENB = None
    SID = None
    
    # to bypass the process_nas() server loop with a custom NAS PDU handler
    RX_HOOK = None
    
    # for pure S1AP procedure (no NAS trafic, neither ERAB-oriented stuff)
    # should we page the UE to run the procedure successfully when UE is idle
    S1AP_FORCE_PAGE = False
    
    #--------------------------------------------------------------------------#
    # global security policy
    #--------------------------------------------------------------------------#
    # this will systematically bypass all auth and smc procedures,
    # NAS MAC and UL count verification in the uplink
    # and setting of the EMM security header (and encryption) in the downlink
    SECNAS_DISABLED = False
    #
    # finer grained NAS security checks:
    # True to drop NAS PDU when NAS MAC verification fails
    SECNAS_MAC = False
    # True to drop NAS PDU when NAS UL count verification fails
    SECNAS_UL  = False
    # WNG: EMM and ESM stacks have further control on accepting or not certain
    # NAS message even if security control have failed
    #
    # this will disable the setting of the EMM security header (and encryption)
    # in the downlink for given NAS message (by name)
    SECNAS_PDU_NOSEC = set()
    #
    # format of the security context dict self.SEC:
    # self.SEC is a dict of available 3G / 4G security contexts indexed by KSI,
    # and current KSI in use
    #
    # when self.SEC['KSI'] is not None, the context is enabled at the NAS level, e.g.
    # self.SEC = {'KSI': 0,
    #             0: {'Kasme': b'...', 'Knasenc': b'...', 'Knasint': b'...', 
    #                 'UL': 0, 'DL': 0, 'EEA': 1, 'EIA': 1,
    #                 'Kenb': b'...', 'CTX': 4},
    #             ...,
    #             'POL': {'TAU': 0, 'SER': 0}}
    # 
    # a single security context contains:
    # Kasme, Kenb: 32 bytes buffer, key used at the NAS layer and sent to the eNB
    #    handling the UE
    # Knasenc, Knasint: 16 bytes buffer, key used at the NAS layer together with
    #    EEA and EIA algorithms
    # UL, DL: NAS UL and DL count
    # EEA, EIA: NAS security algorithms index selected
    # CTX: context of the authentication,
    #    3 means 3G auth converted to 4G context, in this case, CK and IK are also 
    #    available in the security context
    #    4 means 4G auth and native context
    # The POL dict indicates the authentication policy for each procedure
    #
    # in case an E-RAB get activated, but no security context exist
    # we use this dummy AS security context for the eNB
    SECAS_NULL_CTX = {
        'Kenb': 32*b'\0',
        'UESecCap': get_ueseccap_null_alg_lte()
        }
    
    #--------------------------------------------------------------------------#
    # S1APPaging policy
    #--------------------------------------------------------------------------#
    # if we want to page with the IMSI, instead of the (P)TMSI
    PAG_IMSI = False
    #
    # page_block() parameters:
    # number of retries when not successful
    PAG_RETR = 2
    # timer in sec between retries
    PAG_WAIT = 2
    
    #--------------------------------------------------------------------------#
    # S1APInitialContextSetup policy
    #--------------------------------------------------------------------------#
    # to include UERadioCap in request when available (bool)
    ICS_RADCAP_INCL = True
    # to include GUMMEI in request when available (bool)
    ICS_GUMMEI_INCL = True
    # to activate traces (None or dict of values to be passed to the TraceActivation IEs)
    ICS_TRACE_ACT = None
    
    
    
    def _log(self, logtype, msg):
        self.UE._log(logtype, '[UES1d:   %3i] %s' % (self.CtxId, msg))
    
    def __init__(self, ued, enbd=None, ctx_id=-1, sid=None):
        self.UE  = ued
        self.Server = ued.Server
        self.Config = self.Server.ConfigS1
        #
        # dict of ongoing S1AP procedures (indexed by their procedure code)
        self.Proc = {}
        # list of tracked procedures (requires TRACK_PROC = True)
        self._proc = []
        #
        # dict of available LTE security contexts, indexed by KSI
        # and current KSI in use
        self.SEC = {}
        self.reset_sec_ctx()
        #
        self.connected = Event()
        if enbd is not None:
            self.set_ran(enbd)
            self.set_ctx(ctx_id, sid)
        else:
            self.CtxId = -1
        #
        # init EMM and ESM sig stacks
        self.EMM = UEEMMd(ued, self)
        self.ESM = UEESMd(ued, self)
        self.SMS = UESMSd(ued, self)
        #
        # track states for EPS unicast and EMBMS bearers
        self.PDP  = {i: 0 for i in range(16)}
        self.MBMS = {i: 0 for i in range(16)} # to be confirmed if only 16 ctx
    
    def set_ran(self, enbd):
        self.SEC['KSI'] = None
        self.ENB = enbd
        self.connected.set()
    
    def unset_ran(self):
        del self.ENB
        self.SEC['KSI'] = None
        self.clear()
        self.connected.clear()
    
    def set_ran_unconnected(self, enbd):
        # required for paging
        self.SEC['KSI'] = None
        self.ENB = enbd
    
    def unset_ran_unconnected(self):
        # required for paging
        del self.ENB
        self.SEC['KSI'] = None
    
    def is_connected(self):
        #return self.RNC is not None
        return self.connected.is_set()
    
    def set_ctx(self, ctx_id, sid):
        self.CtxId = ctx_id
        self.SID   = sid
    
    def unset_ctx(self):
        self.CtxId = -1
        del self.SID
    
    def reset_sec_ctx(self):
        self.SEC.clear()
        self.SEC['KSI'] = None
        self.SEC['POL'] = {'TAU': 0, 'DET': 0, 'SER': 0}
        if 'UESecCap' in self.UE.Cap:
            del self.UE.Cap['UESecCap']
    
    def get_sec_ctx(self):
        return self.SEC.get(self.SEC['KSI'], None)
    
    #--------------------------------------------------------------------------#
    # handling of S1AP procedures
    #--------------------------------------------------------------------------#
    
    def process_s1ap_pdu(self, pdu_rx):
        """process an S1AP PDU sent by the eNB for UE-associated signalling
        and return a list of S1AP PDU(s) to be sent back to it
        """
        errcause = None
        if pdu_rx[0] == 'initiatingMessage':
            # eNB-initiated procedure, instantiate it
            try:
                Proc = S1APProcEnbDispatcher[pdu_rx[1]['procedureCode']](self)
            except:
                self._log('ERR', 'invalid S1AP PDU, initiatingMessage, code %i'\
                          % pdu_rx[1]['procedureCode'])
                errcause = ('protocol', 'abstract-syntax-error-reject')
                Proc = self.init_s1ap_proc(S1APErrorIndCN, Cause=errcause)
                if not Proc:
                    return []
            else:
                if self.TRACK_PROC:
                    self._proc.append( Proc )
            # process the PDU within the procedure
            Proc.recv( pdu_rx )
            if Proc.Class == 2 and Proc.errcause:
                Err = self.init_s1ap_proc(S1APErrorIndCN, Cause=Proc.errcause)
                if not Err:
                    return []
                self.ProcLast = Err.Code
                return Err.send()
            elif Proc.Class == 1 or errcause:
                self.ProcLast = Proc.Code
                return Proc.send()
            else:
                pdu_tx = []
                for ProcRet in Proc.trigger():
                    pdu_tx.extend( ProcRet.send() )
                    self.ProcLast = ProcRet.Code
                return pdu_tx
        #
        else:
            # CN-initiated procedure, transfer the PDU to it
            try:
                Proc = self.Proc[pdu_rx[1]['procedureCode']]
            except:
                self._log('ERR', 'invalid S1AP PDU, %s, code %i'\
                          % (pdu_rx[0], pdu_rx[1]['procedureCode']))
                errcause = ('protocol', 'message-not-compatible-with-receiver-state')
                Proc = self.init_s1ap_proc(S1APErrorIndCN, Cause=errcause)
                if not Proc:
                    return []
            # process the PDU within the procedure
            Proc.recv( pdu_rx )
            if Proc.errcause:
                Err = self.init_s1ap_proc(S1APErrorIndCN, Cause=Proc.errcause)
                if not Err:
                    return []
                self.ProcLast = Err.Code
                return Err.send()
            elif errcause:
                self.ProcLast = Proc.Code
                return Proc.send()
            else:
                pdu_tx = []
                for ProcRet in Proc.trigger():
                    pdu_tx.extend( ProcRet.send() )
                    self.ProcLast = ProcRet.Code
                return pdu_tx
    
    def init_s1ap_proc(self, ProcClass, **IEs):
        """initialize a CN-initiated S1AP procedure of class `ProcClass' for 
        UE-associated signalling, encode the initiatingMessage PDU with given 
        **IEs and return the procedure
        """
        Proc = self._init_s1ap_proc(ProcClass)
        if not Proc:
            return None
        else:
            self._encode_s1ap_proc(Proc, **IEs)
            return Proc
    
    def _init_s1ap_proc(self, ProcClass):
        if not issubclass(ProcClass, S1APSigProc):
            self._log('WNG', 'starting an invalid procedure for UE-associated S1 signalling')
        if ProcClass.Code in self.Proc:
            self._log('ERR', 'an S1AP procedure %s is already ongoing' % ProcClass.__name__)
            return None
        try:
            Proc = ProcClass(self)
        except:
            # no active S1 link
            self._log('ERR', 'no active S1 link to initialize the S1AP procedure %s'\
                      % ProcClass.__name__)
            return None
        if Proc.Code in S1APProcCnDispatcher and Proc.Class == 1:
            # store the procedure, which requires a response from the eNB
            self.Proc[Proc.Code] = Proc
        if self.TRACK_PROC:
            self._proc.append( Proc )
        return Proc
    
    def _encode_s1ap_proc(self, Proc, **IEs):
        if Proc.Name != 'S1APUEContextRelease':
            IEs['MME_UE_S1AP_ID'], IEs['ENB_UE_S1AP_ID'] = self.CtxId, self.CtxId
        else:
            IEs['UE_S1AP_IDs'] = ('uE-S1AP-ID-pair', {'mME-UE-S1AP-ID': self.CtxId,
                                                      'eNB-UE-S1AP-ID': self.CtxId})
        Proc.encode_pdu('ini', **IEs)
    
    def clear(self):
        # clears all running S1AP procedures
        for Proc in list(self.Proc.values()):
            Proc.abort()
    
    #--------------------------------------------------------------------------#
    # handling of NAS messages dispatching
    #--------------------------------------------------------------------------#
    
    def process_nas(self, buf):
        """process a NAS message buffer for the EPS domain sent by the mobile
        and return a list (possibly empty) of S1AP procedure(s) to be sent back 
        to the eNB
        """
        if self.RX_HOOK:
            return self.RX_HOOK(buf)
        NasRxSec, err = NAS.parse_NASLTE_MO(buf, inner=False)
        if err:
            self._log('WNG', 'invalid EPS NAS message: %s' % hexlify(buf).decode('ascii'))
            return self.ret_s1ap_dnt(NAS.EMMStatus(val={'EMMCause':err}, sec=False))
        #
        # LTE NAS security handling
        sh, pd = NasRxSec['SecHdr'].get_val(), NasRxSec['ProtDisc'].get_val()
        if sh == 0:
            # clear-text NAS message
            NasRxSec._sec   = False
            NasRxSec._ulcnt = 0
            if self.UE.TRACE_NAS_EPS:
                self._log('TRACE_NAS_EPS_UL', '\n' + NasRxSec.show())
            if pd == 7:
                S1apTxProc = self.EMM.process(NasRxSec)
            else:
                assert( pd == 2 ) # this won't happen due to parse_NASLTE_MO()
                S1apTxProc = self.ESM.process(NasRxSec)
        elif sh == 12:
            # NAS service request
            if self.UE.TRACE_NAS_EPS:
                self._log('TRACE_NAS_EPS_UL', '\n' + NasRxSec.show())
            try:
                NasRx, err = self.process_nas_sec_servreq(NasRxSec)
            except Exception as err:
                self._log('ERR', 'unable to process the NAS EMMServiceRequest security, %s' % err)
                return self._s1ap_nas_sec_err()
            if not NasRx:
                return self._s1ap_nas_sec_err()
            else:
                S1apTxProc = self.EMM.process(NasRx)
        elif sh in (1, 2, 3, 4) and pd == 7:
            # security-protected NAS message
            if self.UE.TRACE_NAS_EPS_SEC:
                self._log('TRACE_NAS_EPS_UL_SEC', '\n' + NasRxSec.show())
            try:
                NasRx, err = self.process_nas_sec(NasRxSec, sh)
            except Exception as err:
                self._log('ERR', 'NAS SEC DL: unable to deprotect the NAS message %s' % err)
                return self._s1ap_nas_sec_err()
            if err & 0xff:
                # non-security related error
                self._log('WNG', 'invalid EPS NAS inner message')
                S1apTxProc = self.ret_s1ap_dnt(NAS.EMMStatus(val={'EMMCause':err}, sec=True))
            elif not NasRx:
                # deciphering failed
                return self._s1ap_nas_sec_err()
            else:
                if self.UE.TRACE_NAS_EPS:
                    self._log('TRACE_NAS_EPS_UL', '\n' + NasRx.show())
                if NasRx['ProtDisc'].get_val() == 7:
                    S1apTxProc = self.EMM.process(NasRx)
                else:
                    S1apTxProc = self.ESM.process(NasRx)
        else:
            # cause: invalid mandatory information
            self._log('WNG', 'invalid EPS NAS message: %r' % NasRxSec)
            S1apTxProc = self.ret_s1ap_dnt(NAS.EMMStatus(val={'EMMCause':96}, sec=False))
        #
        return S1apTxProc
    
    def process_nas_sec_servreq(self, ServReq):
        """Check the security on the EMM Service Request.
        Returns the request or None (if security checks are enforced), and the
        security error code.
        
        Security error codes:
        0: no error
        0x100: NAS KSI unknown
        0x200: MAC verification failed
        0x300: NAS UL count not matching
        
        The returned request gets 2 attributes (_sec [bool], _ulcnt [uint])
        """
        if self.SECNAS_DISABLED:
            ServReq._sec   = True
            ServReq._ulcnt = 0
            return ServReq, 0
        #
        ue_ksi, ue_sqn = ServReq['KSI'].get_val(), ServReq['SeqnShort'].get_val()
        if ue_ksi not in self.SEC:
            self._log('WNG', 'NAS SEC: unknown NAS KSI %i in EMMServiceRequest' % ue_ksi)
            self.reset_sec_ctx()
            ServReq._sec   = False
            ServReq._ulcnt = ue_sqn # we are missing the MSB...
            return ServReq, 0x100
        else:
            self.SEC['KSI'] = ue_ksi
        secctx = self.SEC[ue_ksi]
        #
        sqnmsb, sqnlsb = secctx['UL'] & 0xffffffe0, secctx['UL'] & 0x1f
        verif_mac = ServReq.mac_verify(secctx['Knasint'], 0, secctx['EIA'], sqnmsb)
        verif_sqn = True if ue_sqn == sqnlsb else False
        #
        if not verif_mac:
            if self.SECNAS_MAC:
                self._log('ERR', 'NAS SEC UL: MAC short verif failed, dropping EMMServiceRequest')
                return None, 0x200
            else:
                self._log('WNG', 'NAS SEC UL: MAC short verif failed in EMMServiceRequest')
                ServReq._sec   = False
                ServReq._ulcnt = sqnmsb + ue_sqn
                return ServReq, 0x200
        elif not verif_sqn:
            if self.SECNAS_UL:
                self._log('ERR', 'NAS SEC UL: UL count verif failed, dropping EMMServiceRequest')
                return None, 0x300
            else:
                self._log('WNG', 'NAS SEC UL: UL count verif failed in EMMServiceRequest')
                # resynch uplink count
                ServReq._sec   = False
                ServReq._ulcnt = sqnmsb + ue_sqn
                secctx['UL'] = sqnmsb + ue_sqn + 1
                return ServReq, 0x300
        #
        ServReq._sec   = True
        ServReq._ulcnt = secctx['UL']
        secctx['UL']  += 1
        return ServReq, 0
    
    def process_nas_sec(self, NasRxSec, sh):
        """Check the security on all EMM messages, except the Service Request.
        Returns the message or None (if security checks are enforced), and the
        security error code.
        
        Security error codes:
        0: no error
        0x100: NAS KSI unknown
        0x200: MAC verification failed
        0x300: NAS UL count not matching
        
        The returned message gets 2 attributes (_sec [bool], _ulcnt [uint])
        """
        if self.SECNAS_DISABLED:
            # TODO: eventually remove the sec header
            NasRxSec._sec   = True
            NasRxSec._ulcnt = 0
            return NasRxSec, 0
        #
        if 'KSI' in NasRxSec._by_name:
            ue_ksi = NasRxSec['KSI'].get_val()
            if ue_ksi not in self.SEC:
                # UE KSI unknown
                self.reset_sec_ctx()
                if sh in (1, 3):
                    # still, decode the inner NASMessage
                    NasRx, err = NAS.parse_NASLTE_MO(NasRxSec['NASMessage'].get_val(), inner=False)
                    if not err:
                        self._log('WNG', 'NAS SEC UL: unknown NAS KSI %i in %s' % (ue_ksi, NasRx._name))
                        NasRx._sec   = False
                        NasRx._ulcnt = 0
                        return NasRx, 0x100
                # there is nothing we can do here
                self._log('WNG', 'NAS SEC UL: unknown NAS KSI %i, dropping %s' % (ue_ksi, NasRxSec._name))
                return None, 0x100
            else:
                self.SEC['KSI'] = ue_ksi
                secctx = self.SEC[ue_ksi]
        else:
            if self.SEC['KSI'] not in self.SEC:
                # no active KSI: happens when restarting corenet, and UE using a
                # forgotten sec ctx
                if sh in (1, 3):
                    # still, decode the inner NASMessage
                    NasRx, err = NAS.parse_NASLTE_MO(NasRxSec['NASMessage'].get_val(), inner=False)
                    if not err:
                        if self.SEC['KSI'] is not None:
                            self._log('WNG', 'NAS SEC UL: unset NAS KSI for processing %s' % NasRx._name)
                            self.reset_sec_ctx()
                        NasRx._sec   = False
                        NasRx._ulcnt = 0
                        return NasRx, 0x100
                self._log('WNG', 'NAS SEC UL:  unset NAS KSI, dropping %s' % NasRxSec._name)
                return None, 0x100
            else:
                secctx = self.SEC[self.SEC['KSI']]
        #
        sqnmsb, sqnlsb = secctx['UL'] & 0xffffff00, secctx['UL'] & 0xff
        verif_mac = NasRxSec.mac_verify(secctx['Knasint'], 0, secctx['EIA'], sqnmsb)
        ue_sqn    = NasRxSec['Seqn'].get_val()
        verif_sqn = True if ue_sqn == sqnlsb else False
        #
        err = 0
        if not verif_mac:
            if self.SECNAS_MAC:
                self._log('ERR', 'NAS SEC UL: MAC verif failed, dropping %s' % NasRxSec._name)
                return None, 0x200
            else:
                self._log('WNG', 'NAS SEC UL: MAC verif failed in %s' % NasRxSec._name)
                err   = 0x200
                sec   = False
                ulcnt = sqnmsb + ue_sqn
        elif not verif_sqn:
            if self.SECNAS_UL:
                self._log('ERR', 'NAS SEC UL: UL count verif failed, dropping %s' % NasRxSec._name)
                return None, 0x300
            else:
                self._log('WNG', 'NAS SEC UL: UL count verif failed in %s' % NasRxSec._name)
                # resynch uplink count
                secctx['UL'] = sqnmsb + ue_sqn + 1
                err   = 0x300
                sec   = False
                ulcnt = sqnmsb + ue_sqn
        else:
            self._log('DBG', 'NAS SEC UL: MAC verified, UL count %i' % secctx['UL'])
            sec   = True
            ulcnt = secctx['UL']
            secctx['UL'] += 1
        #
        if sh in (2, 4):
            if secctx['EEA'] == 0:
                NasRx, err2 = NAS.parse_NASLTE_MO(NasRxSec[4].get_val(), inner=False)
            else:
                NasRxSec.decrypt(secctx['Knasenc'], 0, secctx['EEA'], sqnmsb)
                NasRx, err2 = NAS.parse_NASLTE_MO(NasRxSec._dec_msg, inner=False)
            if err2:
                # decrypted decoded part is malformed
                err += err2
        else:
            NasRx, err2 = NAS.parse_NASLTE_MO(NasRxSec['NASMessage'].to_bytes(), inner=False)
            if err2:
                # decoded part is malformed
                err += err2
        #
        NasRx._sec   = sec
        NasRx._ulcnt = ulcnt
        return NasRx, err
    
    def output_nas_sec(self, NasTx):
        if self.UE.TRACE_NAS_EPS:
            self._log('TRACE_NAS_EPS_DL', '\n' + NasTx.show())
        if self.SECNAS_DISABLED or NasTx._name in self.SECNAS_PDU_NOSEC or \
        NasTx._sec == False:
            sec = False
        else:
            ksi = self.SEC['KSI']
            if ksi is None:
                # NAS security not activated
                NasTx['SecHdr'].set_val(0)
                sec = False
            elif ksi not in self.SEC:
                # invalid KSI: this should not happen
                self._log('ERR', 'NAS SEC DL: invalid NAS KSI %i, unable to secure the NAS message %s'\
                          % (ksi, NasTx._name))
                self.reset_sec_ctx()
                return None
            else:
                secctx = self.SEC[self.SEC['KSI']]
                sqnmsb, sqnlsb = secctx['DL'] & 0xffffff00, secctx['DL'] & 0xff
                if NasTx._name == 'EMMSecurityModeCommand':
                    # integrity protextion only + new security context
                    sh = 3
                else:
                    # integrity protection + ciphering
                    sh = 2
                try:
                    NasTxSec = NAS.EMMSecProtNASMessage(val={'SecHdr': sh,
                                                             'Seqn': sqnlsb,
                                                             'NASMessage': NasTx.to_bytes()})
                    if sh == 2:
                        NasTxSec.encrypt(secctx['Knasenc'], 1, secctx['EEA'], sqnmsb)
                    NasTxSec.mac_compute(secctx['Knasint'], 1, secctx['EIA'], sqnmsb)
                except:
                    self._log('ERR', 'NAS SEC DL: unable to protect the NAS message %s' % NasTx._name)
                    #self.reset_sec_ctx()
                    return None
                else:
                    secctx['DL'] += 1
                    sec = True
        if sec:
            if self.UE.TRACE_NAS_EPS_SEC:
                self._log('TRACE_NAS_EPS_DL_SEC', '\n' + NasTxSec.show())
            try:
                return NasTxSec.to_bytes()
            except Exception as err:
                self._log('ERR', 'unable to encode the NAS message %s, %r' % (NasTxSec._name, err))
                return None
        else:
            try:
                return NasTx.to_bytes()
            except Exception as err:
                self._log('ERR', 'unable to encode the NAS message %s, %r' % (NasTx._name, err))
                return None
    
    def ret_s1ap_dnt(self, NasTx, **IEs):
        """returns an S1APDownlinkNASTransport procedure initialized with the 
        NAS PDU and optional IEs to be sent
        """
        if not NasTx:
            return []
        else:
            buf = self.output_nas_sec(NasTx)
            if buf is None:
                return self._s1ap_nas_sec_err()
            IEs['NAS_PDU'] = buf
            S1apProc = self.init_s1ap_proc(S1APDownlinkNASTransport, **IEs)
            if S1apProc:
                return [S1apProc]
            else:
                return []
    
    def _s1ap_nas_sec_err(self):
        # TODO: maybe shutdown the S1 link ?
        return []
    
    def clear_nas_proc(self):
        # clears all NAS EPS procedures
        self.EMM.clear()
        self.ESM.clear()
    
    #--------------------------------------------------------------------------#
    # paging and network-initiated procedures' routines
    #--------------------------------------------------------------------------#
    
    def _get_paging_ies(self):
        # prepare the S1APPaging IEs
        return {}
     
    def page(self):
        """sends S1AP Paging command to eNB responsible for the UE TAI
        """
        # send a S1APPaging for the EPS domain
        if self.connected.is_set():
            self._log('DBG', 'paging: UE already connected')
            return
        # get the set of eNBs serving the UE TAI
        tai = (self.UE.PLMN, self.UE.TAC)
        try:
            enbs = [self.Server.RAN[enbid] for enbid in self.Server.TAI[tai]]
        except:
            self._log('ERR', 'paging: no eNB serving the UE TAI %s.%.4x' % tai)
            return
        #
        IEs = self._get_paging_ies(cause)
        # start an S1APPaging procedure on all RNCs
        for enb in enbs:
            enb.page(**IEs)
        self._log('INF', 'paging: ongoing')
    
    def page_block(self, cause=None):
        """Pages the UE and wait for it to connect, or the paging procedure to timeout.
        Returns True if UE gets connected, False otherwise.
        """
        # send a S1APPaging for the EPS domain
        if self.connected.is_set():
            self._log('DBG', 'paging: UE already connected')
            return
        # get the set of eNBs serving the UE TAI
        tai = (self.UE.PLMN, self.UE.TAC)
        try:
            enbs = [self.Server.RAN[enbid] for enbid in self.Server.TAI[tai]]
        except:
            self._log('ERR', 'paging: no eNB serving the UE TAI %s.%.4x' % tai)
            return
        #
        IEs = self._get_paging_ies(cause)
        # retries paging as defined in case UE does not connect
        i = 0
        while i <= self.PAG_RETR:
            # start an S1APPaging procedure on all RNCs
            for enb in enbs:
                enb.page(**IEs)
            # check until UE gets connected or timer expires
            if self.connected.wait(self.PAG_WAIT):
                self._log('INF', 'paging: UE connected')
                return True
            else:
                # timeout
                i += 1
        self._log('WNG', 'paging: timeout, UE not connected')
        return False
    
    # this is used by send_raw() and other network-initiated procedures
    def _net_init_con(self):
        return self.EMM._net_init_con()

